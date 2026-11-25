# 运行维护说明

本版为 v32，继承 v30 的隔离构包和实测比较、v31 的有界 JSON 格式反馈。
构包仍由 Agent 完成。维护命令只检查状态、暂停或验证保全和运行条件，不产生新任务。

## 当前服务器状态

- 项目：`/data/ma2data/task_generate_pipeline`。
- 所有任务保持暂停；两个现用号池为空。不要使用或恢复原所有者的账号。
- 历史 43 条的最后报告仍是 12 份通过、9 条待审、4 条等待资源、10 条未启动、8 条无匹配。
- 已通过的 12 份为实测比较口径，保留原验收记录。此次修复不增加成功任务数量。
- v32 的回归与容器维护检查分别保存。没有新的真实模型构包记录时，不声称完成模型端到端复验。

以下从服务器项目根目录运行，`python` 可换成 Conda 环境的绝对 Python 路径。

## 查看、暂停和恢复

```bash
python task_generate_pipeline/pipeline.py status --project-root "$PWD"
python task_generate_pipeline/pipeline.py pause --project-root "$PWD" --reason '交接暂停'
```

项目根目录的 `USER_PAUSED` 对所有新版本的正式 `plan` 和 `run` 生效。
指定运行目录时，它及项目内的父目录上的 `USER_PAUSED` 也生效。
入口、每次模型动作、容器命令及发布前都会检查；正在执行的子进程可能完成，随后停止。
暂停返回码为 75，已保存的方案和尝试保留，不标成数据或科学失败。
历史冻结版本没有被改写，仍可能缺少此检查。退役的本批次控制脚本禁止直接重跑。

只有项目负责人明确要求恢复后，才执行：

```bash
python task_generate_pipeline/pipeline.py resume --project-root "$PWD" --reason '项目负责人明确授权恢复'
```

`resume` 只移除本层标记，保存历史，不启动工作。如果旧运行目录还存在标记，仍会暂停。
需要恢复该运行时，再明确指定 `--run-dir <原运行目录>`；不要批量删除其他暂停标记。
账号、网络或运行环境没有就绪时，不应开始生成。

## 环境预检和保全检查

```bash
python task_generate_pipeline/pipeline.py preflight --project-root "$PWD" \
  --base-image '<已批准的本地镜像ID>' --require-package numpy \
  --output runs/maintenance/preflight.json
python task_generate_pipeline/pipeline.py audit --project-root "$PWD" \
  --baseline control/preservation-v32-seed-audit-fix.json --output runs/maintenance/preservation.json
```

`preflight` 不用账号，实际启动无网络、只读根目录、去除 capabilities 的诊断容器，
记录镜像、Python、依赖和权限。依赖清单与任务身份一起保存，不能把宿主依赖当容器依赖。
`--require-package` 使用镜像中发行包的名称；成功只证明所列条件，不替代真实任务验收。

`audit` 对照维护时核对并冻结的基线验证原始文件、旧任务包、冻结源码和活动代码。
缺文件或哈希变化都会失败，不能自动接受现状、删除保全检查或复制旧答案绕过。
账号清理回执与清理后的空池哈希已纳入维护基线，不再依赖已删除的个人账号备份。
接手者以后配置自己的账号时，需记录明确授权的迁移并建立新基线；不恢复原所有者的凭据。

## 中断后的任务检查

部署时将已有种子库与分组绑定按相同字节迁入 `config/seed-registry/`，
由 `.pipeline-project.json` 指定；正式 `plan` 默认读写该处，保持冻结源码不变。
新批次和新引擎共用此登记，不建立空映射。登记缺失时报错，不能静默改用另一套种子。

```bash
python task_generate_pipeline/pipeline.py status --project-root "$PWD" \
  --run-dir '<一个真实运行目录>' --plan '<该运行对应的plan.json>' \
  --output runs/maintenance/recovery-status.json
```

它逐条读取 case 状态，验证已发布包与 receipt 的哈希及通过报告，列出未启动和待恢复项。
它不会启动任务；一个运行目录不等于整个历史 43 条，跨运行的对应关系仍查该批 `queue.json`。

同一代码、提示词、数据、种子、镜像和资源身份才使用原命令的 `run --resume`。
已通过的相同身份任务在核对包哈希后跳过，不再调用模型或重建环境。
v30 切换到 v32 后身份变化，必须使用新运行目录；历史方案、失败和任务包保留。
强弱方法同分、缺少可行解、缺标签或模态不符都需要具体科学处理，不能靠通用重试改成通过。

## 模型格式纠正与剩余范围

严格解析 `result_json`，拒绝非法控制字符、NaN/Infinity 和重复键。
格式错误最多反馈一次，先拒绝整次动作，不应用其中的文件修改，再让模型纠正。
纠正有单独的调用余量，不占最后一个有效动作；不会静默修剪内容或修改科学结论。
模型明确报告 `needs_review` 时立即保留原因，不当成格式问题重试。

当前支持离线 CPU 的文件提交与原生评分。执行 Solver 程序、在线 Judge 和特殊工具链
没有因此自动具备；缺这些后端时继续保留未验证状态。
