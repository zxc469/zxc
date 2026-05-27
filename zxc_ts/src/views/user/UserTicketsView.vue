<script setup lang="ts">
const rows = [
  { id: "TK20260411001", category: "退款/退货", status: "pending", updatedAt: "2026-04-11 15:02" },
  { id: "TK20260411002", category: "物流异常", status: "processing", updatedAt: "2026-04-11 18:20" }
];
</script>

<template>
  <h2 class="page-title">我的工单</h2>
  <p class="page-desc">支持查看进度、补充材料、确认关闭。</p>
  <el-card>
    <el-table :data="rows" stripe>
      <el-table-column prop="id" label="工单号" />
      <el-table-column prop="category" label="分类" />
      <el-table-column prop="status" label="状态" />
      <el-table-column prop="updatedAt" label="更新时间" />
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <router-link :to="`/user/tickets/${row.id}`">查看</router-link>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>
