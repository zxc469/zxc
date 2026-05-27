<script setup lang="ts">
import { useRoute } from "vue-router";

const route = useRoute();
</script>

<template>
  <h2 class="page-title">工单详情</h2>
  <p class="page-desc">当前工单号：{{ route.params.id }}</p>
  <el-card>
    <el-descriptions :column="1" border>
      <el-descriptions-item label="状态">processing</el-descriptions-item>
      <el-descriptions-item label="处理客服">客服 A</el-descriptions-item>
      <el-descriptions-item label="问题摘要">物流延迟未更新</el-descriptions-item>
      <el-descriptions-item label="处理记录">已联系仓配，预计 24 小时内更新</el-descriptions-item>
    </el-descriptions>
  </el-card>
</template>
