# task_generate_pipeline 构建说明

PTB 的做法用于构包前的来源冻结与定向改造。NatureGym 的做法用于构包、评估接口和验收。CSV 的原始分类是组身份，每组一个锁定种子。每份数据源的内容、划分和真值从自己的冻结文件派生。

| 阶段 | 谁做什么 | 文件 |
|---|---|---|
| 缺候选时检索 | 程序缓存三个 benchmark 的原始题面与版本。Agent 结合源格式证据查阅题面，明确考核方式、最低数据要求和适配依据；无合适种子则说明缺口。 | [discovery.py](discovery.py) · [seed-discover](skills/seed-discover/SKILL.md) |
| 种子选择与冻结 | 已有候选时 Agent 给整个组选择一个种子；新检索结果通过核验后追加到种子库并直接绑定。程序保存 ID 和规格摘要，已有绑定保持不变。 | [seed-select](skills/seed-select/SKILL.md) · [种子检索说明](SEED_DISCOVERY_GUIDE.md) |
| 来源冻结与任务改造 | 程序解析和校验服务器路径、读取数据结构并记录 SHA-256。Agent 明确原考点、新评测单元、数据角色、提交物和评分证据。 | [sources.py](sources.py) · [task-scope](skills/task-scope/SKILL.md) |
| 构包 | Agent 写数据准备程序、公开说明、私有任务清单、Dockerfile、资源检查脚本和任务专用评分钩子。程序通过容器实际处理数据。 | [task-build](skills/task-build/SKILL.md) · [runtime.py](runtime.py) |
| 元数据 | Agent 根据真实文件、镜像和校准观察补全私有元数据和公开说明。 | [task-metadata](skills/task-metadata/SKILL.md) |
| 测试案例 | 独立 Agent 写案例准备程序，生成正确、错误和格式不合法的控制提交，再调用原生格式检查和评分。准备程序执行失败时可单独返修一次，保留旧程序和日志；不改任务包或预期科学结果。 | [verification-cases](skills/verification-cases/SKILL.md) · [verification.py](verification.py) · [pipeline.py](pipeline.py) |
| 独立验收 | 原生静态检查器和 Docker 记录提供事实。另一次 Agent 调用按 NatureGym 的 36 项要求核对实际包，管线合并模型结论和程序证据。 | [task-verify](skills/task-verify/SKILL.md) |
| 返修与发布 | 确认缺陷后，修复 Agent 编辑下一轮候选的源码。程序重新准备数据和执行检查，通过才保存发布版。 | [task-fix](skills/task-fix/SKILL.md) · [pipeline.py](pipeline.py) |

## Agent 怎样操作文件

每个阶段有自己的 Markdown 说明。Codex 返回可核验的读写请求和结果，`agent.py` 按当前阶段的文件权限应用请求，并记录提示词摘要、调用和可用的 token 统计。Agent 可以编写完整的阶段产物。生成的 Python 和环境脚本在隔离容器里执行，原始数据按文件只读挂载。Solver 容器只挂载 `input/`。格式检查只挂载精简的提交映射与格式钩子，评分环境才挂载私有资产。容器运行证据、原始标签和完整运行日志留在本地运行目录。

## NatureGym 原生接口

任务目录包含 `task.source.toml`、`task.json`、`metadata.json`、`input/`、`evaluation/`、`environment/`。`reference.py` 从 [vendor/naturegym](vendor/naturegym/README.md) 加载并校验固定版本的上游文件，再将原版 `evaluator.py` 写入任务的 `evaluation/`。任务 Agent 按统一接口生成 `metrics.py`、数据绑定、必要的规则 rubric 和环境。原生静态检查涵盖的结构项和全部 36 项独立验收分开记录。Windows 上的静态检查使用路径分隔符和导入兼容处理，生成的原生评估器字节保持一致。

本地回归可直接执行 `python -m unittest discover -s task_generate_pipeline/tests -v`。测试使用合成数据和模拟容器覆盖调度，并直接运行原生静态检查器、格式检查和评分计算。Windows 宿主的合成评分测试不验证 Linux 容器权限。


## 2026-11-19 补齐的运行与审计

- profiles.py 读取 H5AD 的矩阵、字段、标签统计、空间信息和内嵌引用。确认来源和字段含义后，Agent 才设计任务。CELLxGENE 规则以声明的 schema 版本为依据，数值检查与实验事实分别记录。
- contracts.py 向各阶段提供原生格式要求、读写路径、完整错误信息和独立测试协议。返修时会带上上一轮的实跑记录。
- runtime.py 在项目内创建经 SHA-256 校验的数据工作副本。原始数据只读。准备程序的辅助模块有明确挂载位置。
- 环境检查保存镜像 ID、镜像层、依赖导入和 pip check 结果。Solver 实测只挂载公开 input，检查私有目录不可见、公开输入不可写和临时输出可写。
- 独立测试 Agent 的 make_cases.py 会读取冻结的原始数据与实际任务包，生成 source_audit.json，检查来源对应、私有参考结果和公开材料边界。它同时生成 3–32 条正确、错误与非法提交的控制测试。
- 原生评估器分别执行格式检查、正常判分，并额外在包外、使用独立公开数据根目录运行正确控制。原生 evaluator.py 和上游 36 项验收规则保持固定版本。
- 私有审计材料仅供构建和验收侧读取。Solver 的题面和数据不包含这些材料。只有 pass 或 pass_with_warnings 的包进入 passed；未验证和失败结果均保留草稿与原因。

关键文件在 cases/<任务ID>/attempt-*/evidence/ 和 verification/。环境报告、来源审计、实际判分和模型独立审查需要结合查看。回归测试使用合成数据，真实数据源的完整验收另行记录。
# 准备阶段的独立方法执行

当强、弱参考方法必须只接触公开输入时，模型可以把准备工作分成三步：`work/prepare.py` 先整理公开数据并声明方法；`work/prepare_lib/methods/<id>/` 放每个方法的程序；`work/prepare_lib/finalize.py` 最后读取真实方法输出，检查可行性并汇总私有参考和实测分数。

`authoring_methods.py` 读取 `/out/evidence/method-jobs.json`，通过 `runtime.py` 为每个方法启动独立容器。方法只能看到公开数据、自己的程序和自己的空输出目录，不能看到原始文件、私有答案或另一方法的输出。全部步骤共用准备阶段的时间预算。主机保留执行回执，并把原封不动的方法程序和输出放进私有的 `host-method-runs/`。这一步提供执行和隔离证据；任务仍须通过原有的科学检查、判分测试和独立验收。
