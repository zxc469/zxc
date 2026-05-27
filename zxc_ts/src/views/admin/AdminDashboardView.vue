<template>
  <h2 class="page-title">运营看板</h2>
  <p class="page-desc">后续接入问答命中率、人工接管率、工单统计等指标。</p>
  <el-row :gutter="16">
    <el-col :span="8"><el-statistic title="今日会话数" :value="86" /></el-col>
    <el-col :span="8"><el-statistic title="人工接管率(%)" :value="23.4" /></el-col>
    <el-col :span="8"><el-statistic title="工单关闭率(%)" :value="77.1" /></el-col>
  </el-row>
</template>
