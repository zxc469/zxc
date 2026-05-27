<script setup lang="ts">
const rows = [
  { id: "TK20260411001", assignee: "客服 A", status: "pending" },
  { id: "TK20260411002", assignee: "客服 B", status: "processing" }
];
</script>

<template>
  <h2 class="page-title">客服工单处理</h2>
  <p class="page-desc">支持分配、状态流转、内部备注。</p>
  <el-card>
    <el-table :data="rows" stripe>
      <el-table-column prop="id" label="工单号" />
      <el-table-column prop="assignee" label="处理人" />
      <el-table-column prop="status" label="状态" />
      <el-table-column label="操作">
        <template #default>
          <el-button type="primary" link>更新状态</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>
