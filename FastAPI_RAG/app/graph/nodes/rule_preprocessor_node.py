"""规则前置节点：安全拦截、噪声过滤、快速回复、快速通道分流。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from langchain_core.messages import HumanMessage

from app.config.rule_preprocessor_config import DEFAULT_RULE_PREPROCESSOR_CONFIG, RulePreprocessorConfig
from app.graph.models.graph_state import GraphState
from app.graph.models.protocol_models import RuleDecision, RuleDecisionType
from app.utils.logger import get_logger

logger = get_logger(__name__)


class RulePreprocessor:
    """规则前置引擎：按安全→噪声→快速回复→快速通道顺序短路执行，命中即返回。"""

    def __init__(self, config: RulePreprocessorConfig | None = None) -> None:
        self.config = config or DEFAULT_RULE_PREPROCESSOR_CONFIG

    # ── 主入口 ──────────────────────────────────────────────

    def process(self, user_message: str) -> RuleDecision:
        """对用户输入串联执行四层规则检测，首个命中即短路返回。"""
        original = user_message.strip()

        block_decision = self._security_block(original)
        if block_decision is not None:
            return block_decision

        noise_decision = self._noise_filter(original)
        if noise_decision is not None:
            return noise_decision

        quick_reply_decision = self._quick_reply(original)
        if quick_reply_decision is not None:
            return quick_reply_decision

        fast_track_decision = self._fast_track(original)
        if fast_track_decision is not None:
            return fast_track_decision

        return RuleDecision(
            decision_type=RuleDecisionType.PASS_TO_LLM,
            matched_rule="pass_to_llm_default",
        )

    def process_for_graph(self, user_message: str) -> dict[str, Any]:
        """将规则决策转换为 GraphState 写入字段。"""
        decision = self.process(user_message=user_message)
        state_patch: dict[str, Any] = {
            "malicious_flag": decision.decision_type == RuleDecisionType.BLOCK,
            "rule_decision": decision,
        }

        if decision.decision_type == RuleDecisionType.HUMAN_HANDOFF:
            state_patch["need_human"] = True
            return state_patch

        if decision.decision_type in {RuleDecisionType.BLOCK, RuleDecisionType.DIRECT_REPLY}:
            state_patch["final_answer"] = decision.answer
            return state_patch

        return state_patch

    # ── 四层规则 ────────────────────────────────────────────

    def _security_block(self, text: str) -> RuleDecision | None:
        """检测违规内容：敏感词、SQL注入/XSS、广告词、非白名单外链。"""
        if not text:
            return None

        lowered = text.lower()
        reply = self.config.security.block_reply

        for blocked_word in self.config.security.blocked_words:
            if blocked_word.lower() in lowered:
                return RuleDecision(decision_type=RuleDecisionType.BLOCK, answer=reply, matched_rule="security_blocked_word")

        for pattern in self.config.security.sql_injection_patterns + self.config.security.xss_patterns:
            if re.search(pattern, text):
                return RuleDecision(decision_type=RuleDecisionType.BLOCK, answer=reply, matched_rule="security_attack_pattern")

        for ad_word in self.config.security.ad_words:
            if ad_word.lower() in lowered:
                return RuleDecision(decision_type=RuleDecisionType.BLOCK, answer=reply, matched_rule="security_advertisement")

        links = re.findall(r"(https?://[^\s]+|www\.[^\s]+)", text, flags=re.IGNORECASE)
        if links:
            whitelist = [d.lower() for d in self.config.security.allow_link_domains]
            for raw_link in links:
                link = raw_link if raw_link.startswith(("http://", "https://")) else f"https://{raw_link}"
                domain = urlparse(link).netloc.lower()
                if not domain or not any(domain == allowed or domain.endswith(f".{allowed}") for allowed in whitelist):
                    return RuleDecision(decision_type=RuleDecisionType.BLOCK, answer=reply, matched_rule="security_external_link")

        return None

    def _noise_filter(self, text: str) -> RuleDecision | None:
        """过滤无意义输入：空白、纯符号、纯数字、纯英文、重复字符、过短文本。"""
        compact = re.sub(r"\s+", "", text).strip()
        if not compact:
            return self._direct_reply("noise_blank", self.config.noise.invalid_reply)

        # 纯符号（不含字母、数字、中文字符）
        if all(
            not char.isalnum()
            and not ("一" <= char <= "鿿" or "㐀" <= char <= "䶿" or "豈" <= char <= "﫿")
            for char in compact
        ):
            return self._direct_reply("noise_symbols", self.config.noise.invalid_reply)

        if compact.isdigit():
            return self._direct_reply("noise_digits", self.config.noise.invalid_reply)

        # 纯 ASCII 字母，但不是快速回复词（如 hi/hello/thx），当成噪声
        if bool(re.fullmatch(r"[A-Za-z]+", compact)):
            quick_cfg = self.config.quick_reply
            quick_words = (
                {self._normalize_for_exact_match(w) for w in quick_cfg.greeting_words}
                | {self._normalize_for_exact_match(w) for w in quick_cfg.thanks_words}
                | {self._normalize_for_exact_match(w) for w in quick_cfg.goodbye_words}
                | {self._normalize_for_exact_match(w) for w in quick_cfg.apology_words}
            )
            if self._normalize_for_exact_match(text) not in quick_words:
                return self._direct_reply("noise_letters", self.config.noise.invalid_reply)

        if len(set(compact)) == 1 and len(compact) >= self.config.noise.repeat_length_threshold:
            return self._direct_reply("noise_repeated", self.config.noise.invalid_reply)

        if len(compact) < self.config.noise.too_short_length and compact not in self.config.noise.allow_short_words:
            return self._direct_reply("noise_too_short", self.config.noise.invalid_reply)

        return None

    def _quick_reply(self, text: str) -> RuleDecision | None:
        """匹配快速回复场景：系统命令 > 问候/感谢/再见/道歉 > 固定信息。"""
        normalized = self._normalize_for_exact_match(text)
        quick_cfg = self.config.quick_reply

        # 系统命令
        for action, rule in quick_cfg.system_command_rules.items():
            keywords = {self._normalize_for_exact_match(str(k)) for k in rule.get("keywords", [])}
            if normalized in keywords:
                return self._direct_reply(f"quick_system_command_{action}", str(rule.get("reply", "")))

        # 社交礼仪
        if normalized in {self._normalize_for_exact_match(w) for w in quick_cfg.greeting_words}:
            return self._direct_reply("quick_greeting", quick_cfg.greeting_reply)
        if normalized in {self._normalize_for_exact_match(w) for w in quick_cfg.thanks_words}:
            return self._direct_reply("quick_thanks", quick_cfg.thanks_reply)
        if normalized in {self._normalize_for_exact_match(w) for w in quick_cfg.goodbye_words}:
            return self._direct_reply("quick_goodbye", quick_cfg.goodbye_reply)
        if normalized in {self._normalize_for_exact_match(w) for w in quick_cfg.apology_words}:
            return self._direct_reply("quick_apology", quick_cfg.apology_reply)

        # 固定信息
        lowered = text.lower()
        for rule_id, rule in quick_cfg.fixed_info_rules.items():
            keywords = [str(k).lower() for k in rule.get("keywords", [])]
            if any(keyword in lowered for keyword in keywords):
                return self._direct_reply(f"fixed_info_{rule_id}", str(rule.get("reply", "")))

        return None

    def _fast_track(self, text: str) -> RuleDecision | None:
        """高置信度意图快速路由：human_handoff → HUMAN_HANDOFF，其余 → PASS_TO_LLM。"""
        fast_cfg = self.config.fast_track

        matched_intents: list[str] = []
        for intent in fast_cfg.intent_priority:
            keywords = [k.lower() for k in fast_cfg.intent_keywords.get(intent, [])]
            if any(self._is_confident_fast_track_hit(text, keyword) for keyword in keywords):
                matched_intents.append(intent)

        if len(matched_intents) != 1:
            return None

        selected_intent = matched_intents[0]
        if self._hit_negation(text, selected_intent):
            return RuleDecision(
                decision_type=RuleDecisionType.PASS_TO_LLM,
                matched_rule=f"fast_track_{selected_intent}_negated",
            )

        if selected_intent == "human_handoff":
            return RuleDecision(
                decision_type=RuleDecisionType.HUMAN_HANDOFF,
                matched_rule=f"fast_track_{selected_intent}",
            )

        return RuleDecision(
            decision_type=RuleDecisionType.PASS_TO_LLM,
            matched_rule=f"fast_track_{selected_intent}",
        )

    # ── 辅助 ────────────────────────────────────────────────

    def _direct_reply(self, rule_id: str, answer: str) -> RuleDecision:
        """构造 DIRECT_REPLY 决策。"""
        return RuleDecision(
            decision_type=RuleDecisionType.DIRECT_REPLY,
            answer=answer,
            matched_rule=rule_id,
        )

    def _hit_negation(self, text: str, intent: str) -> bool:
        """检测快速通道命中是否被否定词驳回。"""
        lowered = text.lower()
        fast_cfg = self.config.fast_track
        if any(word.lower() in lowered for word in fast_cfg.global_negation_words):
            return True
        intent_negation = [word.lower() for word in fast_cfg.intent_negation_blacklist.get(intent, [])]
        return any(word in lowered for word in intent_negation)

    @staticmethod
    def _normalize_for_exact_match(text: str) -> str:
        """文本归一化：小写 + 去标点空格。"""
        lowered = text.lower().strip()
        return re.sub(r"[，。！？；,.!?;\s]+", "", lowered)

    def _is_confident_fast_track_hit(self, text: str, keyword: str) -> bool:
        """高置信度关键词匹配：完全相等，或首/尾匹配且差异≤4字符，或中间包含且总差异≤4字符。"""
        normalized_text = self._normalize_for_exact_match(text)
        normalized_keyword = self._normalize_for_exact_match(keyword)
        if not normalized_keyword:
            return False

        if normalized_text == normalized_keyword:
            return True

        extra = len(normalized_text) - len(normalized_keyword)
        if extra <= 0:
            return False

        if normalized_text.startswith(normalized_keyword) or normalized_text.endswith(normalized_keyword):
            return extra <= 4

        # 关键词在中间：如"我想申请退款"包含"退款"，总差异 ≤ 4 也算高置信度
        if normalized_keyword in normalized_text and extra <= 4:
            return True

        return False


# ── 模块级实例 ──────────────────────────────────────────────

_engine: RulePreprocessor = RulePreprocessor()


async def node_rule_preprocessor(state: GraphState) -> GraphState:
    """LangGraph 入口节点：取用户消息 → 规则前置处理 → 写回 GraphState。"""
    try:
        user_message = ""
        for msg in reversed(state.get("messages", [])):
            if isinstance(msg, HumanMessage):
                user_message = str(msg.content)
                break

        logger.info("rule_preprocessor 开始处理 | user_message=%s", user_message)

        patch = _engine.process_for_graph(user_message=user_message)

        merged_state: GraphState = {**state, **patch}

        decision = merged_state.get("rule_decision")
        dt = getattr(decision, "decision_type", "")
        mr = getattr(decision, "matched_rule", "")
        ans = str(merged_state.get("final_answer", "") or "")
        logger.info("rule_preprocessor 规则决策 | decision=%s rule=%s answer=%s", dt, mr, ans[:80])
        logger.info(
            "rule_preprocessor 处理完毕 | decision_type=%s matched_rule=%s need_human=%s has_final_answer=%s",
            dt, mr,
            bool(merged_state.get("need_human", False)),
            bool(ans),
        )
        return merged_state

    except Exception as exc:
        logger.warning("规则前置节点执行异常，返回 has_error 状态由图条件边路由至降级节点。", exc_info=True)
        return {
            **state,
            "has_error": True,
            "current_failed_node": "rule_preprocessor",
            "error_msg": str(exc),
        }
