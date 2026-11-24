# 新任务的实测比较口径

正式构建新任务时，在 `pipeline.py run` 后增加 `--comparison-profile generated_measured`。
没有该参数时仍采用 `strict_upstream_reported`，要求对应的已报道比较结果。
切换口径后必须使用新的运行目录，不能复用旧目录的验收签名。

实测口径保留 NatureGym 的任务格式、原始来源核查、科学约束、公开/私有隔离、
真实容器运行和独立正负例验证。只把“必须有对应历史报道成绩”改为同一冻结任务上
实跑强参考与合理弱基线。不能编造分数，不能从种子题搬成绩，也不能把读取私有答案
的程序作为解题参考。原始 NatureGym 参考规则仍原样保留在 vendor 中。

模型在准备程序中实跑方法，将数值和条件写入 `/out/evidence/calibration.json`。
元数据必须标记口径、实测来源、Solver 信息边界和对应的 calibration ID；宿主程序
校验声明与数值链接，独立验证模型继续审查具体算法、划分、指标、日志和信息边界。
原评分接口的 `is_sota=true` 在此口径中只选择实测归一化锚点，必须同时标记
`literature_sota=false`，不能解释成文献 SOTA 或已证明的全局最优。

`comparisons.py` 实现声明和观测链接检查，`contracts.py` 提供两种口径的提示，
`pipeline.py` 将口径纳入冻结身份并执行检查。通过后的包目录额外保存
`comparison-profile.json`，实际报告也标明实测口径。严格原始规则是否完整通过另行记录。

旧任务包和旧报告保留。实测版需要重新构建、执行和独立验收，不能只改一行状态。
