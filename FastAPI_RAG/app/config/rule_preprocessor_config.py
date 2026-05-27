"""规则预处理器配置模型。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SecurityLayerConfig:
    blocked_words: list[str] = field(
        default_factory=lambda: [
            "恐怖袭击",
            "制毒",
            "仇恨言论",
            "黄播",
            "开盒",
        ]
    )
    sql_injection_patterns: list[str] = field(
        default_factory=lambda: [
            r"(?i)\bunion\s+select\b",
            r"(?i)\bdrop\s+table\b",
            r"(?i)\bor\s+1\s*=\s*1\b",
            r"(?i)\binsert\s+into\b",
            r"(?i)\bdelete\s+from\b",
        ]
    )
    xss_patterns: list[str] = field(
        default_factory=lambda: [
            r"(?i)<script\b",
            r"(?i)javascript:",
            r"(?i)onerror\s*=",
            r"(?i)onload\s*=",
        ]
    )
    ad_words: list[str] = field(
        default_factory=lambda: [
            "加vx",
            "加微信",
            "返利",
            "稳赚",
            "兼职刷单",
            "代开发票",
        ]
    )
    allow_link_domains: list[str] = field(default_factory=lambda: ["example.com"])
    block_reply: str = "抱歉，您的输入包含违规内容，请文明发言。"


@dataclass(frozen=True)
class NoiseFilterConfig:
    invalid_reply: str = "抱歉，我无法理解您的输入，请用文字清晰描述您的问题。"
    too_short_length: int = 2
    repeat_length_threshold: int = 4
    allow_short_words: list[str] = field(default_factory=lambda: ["在", "好"])


@dataclass(frozen=True)
class QuickReplyConfig:
    greeting_words: list[str] = field(default_factory=lambda: ["你好", "您好", "hello", "hi", "在吗", "有人吗", "哈喽"])
    thanks_words: list[str] = field(default_factory=lambda: ["谢谢", "感谢", "thanks", "thx", "辛苦了"])
    goodbye_words: list[str] = field(default_factory=lambda: ["再见", "拜拜", "bye", "下次见", "先这样", "不聊了"])
    apology_words: list[str] = field(default_factory=lambda: ["对不起", "抱歉"])
    greeting_reply: str = "你好，我在这儿。你可以告诉我具体遇到的问题。"
    thanks_reply: str = "不客气，很高兴帮到你。还需要我继续协助吗？"
    goodbye_reply: str = "好的，已记录本次会话。后续有问题随时找我。"
    apology_reply: str = "没关系，我理解你的心情。你可以继续描述你的问题。"
    fixed_info_rules: dict[str, dict[str, list[str] | str]] = field(
        default_factory=lambda: {
            "work_time": {
                "keywords": ["工作时间", "几点上班", "几点下班", "客服时间"],
                "reply": "我们的客服工作时间是每天 9:00-21:00。",
            },
            "company_address": {
                "keywords": ["公司地址", "你们在哪", "地址在哪里"],
                "reply": "我们的公司地址是北京市朝阳区 XX 路 XX 号。",
            },
            "delivery_area": {
                "keywords": ["配送范围", "哪些地区能送", "支持配送地区", "支持哪些地区配送"],
                "reply": "我们支持全国大部分地区的配送。",
            },
            "after_sales_policy": {
                "keywords": ["售后政策", "退换货政策", "七天无理由", "7天无理由"],
                "reply": "我们提供 7 天无理由退换货服务。",
            },
        }
    )
    system_command_rules: dict[str, dict[str, str | list[str]]] = field(
        default_factory=lambda: {
            "end_session": {
                "keywords": ["结束", "关闭", "退出"],
                "reply": "后续你有任何问题，随时再来找我，我会继续协助你。",
            },
            "clear_history": {
                "keywords": ["清除历史", "删除记录"],
                "reply": "后续你有任何问题，随时再来找我，我会继续协助你。如果你想继续咨询，直接发我你的问题就可以。",
            },
        }
    )


@dataclass(frozen=True)
class FastTrackConfig:
    global_negation_words: list[str] = field(default_factory=lambda: ["不要", "不用", "不想", "不需要", "别"])
    intent_priority: list[str] = field(
        default_factory=lambda: ["human_handoff", "after_sales", "complaint", "order_query", "logistics_query"]
    )
    intent_keywords: dict[str, list[str]] = field(
        default_factory=lambda: {
            "human_handoff": ["转人工", "找客服", "人工服务", "在线客服", "人工客服", "真人客服"],
            "after_sales": ["退款", "申请退款", "我要退款"],
            "complaint": ["投诉", "我要投诉"],
            "order_query": ["查订单", "我的订单"],
            "logistics_query": ["查物流", "我的快递"],
        }
    )
    intent_negation_blacklist: dict[str, list[str]] = field(
        default_factory=lambda: {
            "human_handoff": ["不要人工", "不用人工", "不需要人工"],
            "after_sales": ["不用退款", "不要退款"],
            "complaint": ["不投诉", "不用投诉"],
            "order_query": [],
            "logistics_query": [],
        }
    )


@dataclass(frozen=True)
class RulePreprocessorConfig:
    security: SecurityLayerConfig = field(default_factory=SecurityLayerConfig)
    noise: NoiseFilterConfig = field(default_factory=NoiseFilterConfig)
    quick_reply: QuickReplyConfig = field(default_factory=QuickReplyConfig)
    fast_track: FastTrackConfig = field(default_factory=FastTrackConfig)


DEFAULT_RULE_PREPROCESSOR_CONFIG = RulePreprocessorConfig()
