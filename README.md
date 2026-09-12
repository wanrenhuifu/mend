# Mend

> **一个会自己跑测试证明改对了的 coding agent —— 不重写，只修补。**

Mend is a coding agent that repairs an existing codebase: it reads the repo, makes the smallest correct change, then **proves the fix by running the tests**.

「改好了」不算完成，**测试从红变绿才算完成**。

---

## 它做什么

```bash
mend run "登录接口在超时后没有重试，修一下"
```

1. **看地图**：先给模型一份仓库摘要（目录 + 文件名 + 行数），再按任务关键词初筛出少数候选文件 —— 不是把整个仓库塞进上下文。
2. **精确改**：用 `edit_file` 做「唯一匹配」的字符串替换，产出尽可能小的 diff，而不是重写整个文件。
3. **跑测试**：改完进入验证门。测试不绿就不允许结束，报错原文回灌给它继续改；连续两次同样的失败会提示它换思路。仓库里没有可跑的测试时，它不会假装完成，而是明确告诉你「无法验证」。
4. **留轨迹**：每一步（读了什么、改了什么、测试输出是什么）都写进 `.mend/runs/*.jsonl`，可以完整回放。

## 30 秒跑起来

```bash
python -m mend doctor                      # 环境自检
python -m mend eval --fake                 # 离线跑通完整闭环（不需要密钥、不需要网络）
python -m mend plan --fake "看看这个仓库"    # 看 agent 的上下文、可用工具
python -m mend trace --list                # 看历史运行记录，挑一次回放
```

核心零依赖，只要 Python >= 3.11 就能跑。接上真实模型：

```bash
export MEND_API_KEY=sk-xxx                 # Windows PowerShell: $env:MEND_API_KEY="sk-xxx"
python -m mend run "把用户列表接口的分页参数从 page 改成 offset"
```

想直接用 `mend` 命令（而不是 `python -m mend`）：`pip install -e ".[dev]"`。

## CLI

| 命令 | 作用 | 工具权限 |
|---|---|---|
| `mend plan "任务"` | 只读：输出上下文 + 可用工具 + 计划，不改代码 | read |
| `mend run "任务"` | 完整闭环：读 -> 改 -> 跑测试 -> 迭代 | read / write / shell |
| `mend fix` | 把当前失败的测试修到通过 | read / write / shell |
| `mend review` | 审查当前 diff，只给意见 | read |
| `mend eval` | 跑评测集：通过率 / 步数 / 耗时 | read / write / shell |
| `mend trace [id]` | 列出或回放历史运行轨迹 | - |
| `mend doctor` | 环境自检 | - |
| `mend init` | 生成 mend.toml | - |

## 一次运行发生了什么

```
任务
 ├─ context  仓库地图 + 关键词初筛         context.py
 ├─ llm      模型决定下一步                 llm.py
 ├─ tool     执行工具调用，结果回灌给模型    tools.py
 │    read    list_dir / read_file / search / git_status / git_diff
 │    write   edit_file / write_file            <- 只在 run / fix 里挂载
 │    shell   run_command（白名单 + 超时）       <- 只在 run / fix 里挂载
 ├─ verify   声称完成就必须跑测试            verify.py
 │    通过 -> 结束；失败 -> 回灌报错，回到 llm
 └─ stop     输出说明 + diff + 轨迹
```

## 输出长什么样

跑一次是实时逐行往下滚的（每步一行，失败会多打几行报错），跑完给报告。想回看就 `mend trace`：

```
运行  eval-off_by_one-181355
任务  calc.py 里的 average() 算错了：请修好它，不要修改测试。
仓库  D:\Code
开始  2026-09-12 18:13:55
结果  done  测试通过  有改动  步数 6  用时 2.2s

#0  ok   context repo_map     +  0.1s      0ms  候选文件: test_calc.py, calc.py, fake_script.json
#1  ok   llm     step1        +  0.1s      0ms  -> read_file
#2  ok   tool    read_file    +  0.1s      0ms  calc.py 共 13 行，显示 1-13：
#3  ok   llm     step2        +  0.1s      0ms  -> read_file
#4  ok   tool    read_file    +  0.1s      0ms  test_calc.py 共 13 行，显示 1-13：
#5  ok   llm     step3        +  0.1s      0ms  -> run_command
#6  FAIL tool    run_command  +  0.6s    546ms  exit=1 (544ms)
      ...
      === short test summary info ===========================
      FAILED test_calc.py::test_average_of_three - assert 1.0 == 2
      FAILED test_calc.py::test_average_of_single - assert 0.0 == 5
      2 failed, 1 passed in 0.09s
#7  ok   llm     step4        +  0.6s      0ms  -> edit_file
#8  ok   tool    edit_file    +  0.6s      0ms  已修改 calc.py
#9  ok   llm     step5        +  0.6s      0ms  -> run_command
#10 ok   tool    run_command  +  1.1s    522ms  exit=0 (521ms)
#11 ok   llm     step6        +  1.1s      0ms  average() 的循环少算了最后一个元素（numbers[:-1]）。已改成遍历全部元素，3 个测试全部通过。
#12 ok   tool    run_command  +  1.6s    490ms  exit=0 (489ms)
#13 ok   verify  python -m pytest -q +  1.6s    490ms  通过 3 passed in 0.03s
#14 ok   stop    done         +  1.6s      0ms  验证=通过  改动=有
#15 ok   judge   expect=pass  +  2.2s      0ms  通过
```

前三列是序号 / 成败 / 类型（`context` 组装上下文、`llm` 模型决策、`tool` 工具调用、`verify` 验证门、`judge` 评测判定），后面是相对开始的时刻、耗时和一句话摘要；失败的那一步会额外把报错末尾几行打出来。

输出层的细节：颜色只在 TTY 下输出（管道和 CI 日志里是纯文本），工具名按权限级别上色（read 蓝 / write 黄 / shell 紫）；想要控制可以用 `--color always|never`，或者按惯例设 `NO_COLOR` / `FORCE_COLOR`。报告里的"改动"只包含**这次运行改过的文件**，你自己手改的东西不会被算成它的产出。

## 设计取舍（面试会问的五个问题）

**1. 为什么不用 LangChain？**
核心只有三件事：组装上下文、调度工具、判断什么时候算完。自己写（这个仓库 1500 行左右）才能在面试里讲清每一步的失败模式，也才能让别人 30 秒复现。框架替你省掉的部分，往往正是 agent 最难的部分。

**2. 怎么防止「改测试让测试变绿」？**
目前是提示词明确禁止 + 轨迹留痕（review 时能发现）。这还不够硬 —— 下一步是在工具层把测试文件设成不可写，做成结构保证。这条列在 Roadmap 里，也写在 [docs/design.md](docs/design.md) 的「已知弱点」。

**3. 只读模式怎么保证？**
不是靠提示词求模型别乱改，而是工具分级挂载：`plan` / `review` 只挂 read 级工具，写工具既不在模型的 schema 里，真调了也会被 `registry.call()` 挡掉。

**4. 上下文怎么选？**
先给地图（每个文件多少行），再按任务关键词打分初筛（英文按单词切，中文用 2-gram，因为没有词边界），读文件默认只给 400 行。上下文长度 = 成本 = 延迟，也是 agent 最常见的失败来源。

**5. 为什么零依赖？**
标准库够用：`urllib` 调 API、`argparse` 做 CLI、`tomllib` 读配置。面试官 `git clone` 之后不建虚拟环境就能跑，这比多几个漂亮的依赖值钱。

## 评测

```bash
python -m mend eval --fake      # 离线：用任务自带剧本验证整条流水线
python -m mend eval --json      # 接上真实模型后：输出可统计的结果
```

一个任务 = 一个坏仓库（`evals/fixtures/`）+ 一句任务描述 + 一条判定命令。评测时 fixture 会被复制到临时目录并 `git init`，**你的原仓库全程不动**。目前带了一个示例任务 `off_by_one`。

## 配置

`mend init` 生成 `mend.toml`（带注释的完整版本见 [mend.example.toml](mend.example.toml)）。密钥只从环境变量或 `.env` 读，永远不写进配置文件。

## 目录结构

```
mend/
├── cli.py        # 8 个子命令
├── agent.py      # 主循环：验证门 / 防打转 / 步数预算
├── tools.py      # 工具层：路径安全 + 三级权限 + 命令白名单
├── context.py    # 仓库地图 + 关键词初筛
├── verify.py     # 跑测试并判定
├── llm.py        # LLM 抽象 + OpenAI 兼容实现 + 离线假模型
├── ui.py         # 输出层：颜色 / 运行记录排版 / diff 上色
├── trace.py      # 轨迹落盘与回放
├── evaluator.py  # 评测集
├── config.py     # 配置：默认值 <- mend.toml <- 环境变量
└── vcs.py        # git 封装
```

## Roadmap

- [ ] 测试文件不可写（结构上防止改测试作弊）
- [ ] 多文件改动 + 可回滚的事务
- [ ] 用 tree-sitter 做符号索引，替代关键词初筛
- [ ] 按 diff 指纹缓存验证结果，避免重复跑测试
- [ ] 评测集扩到 10+ 个任务，输出成功率 / 步数 / 成本曲线

## License

MIT
