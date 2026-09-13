# Mend

[![CI](https://github.com/wanrenhuifu/mend/actions/workflows/ci.yml/badge.svg)](https://github.com/wanrenhuifu/mend/actions/workflows/ci.yml)

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
3. **跑测试**：改完进入验证门。测试不绿就不允许结束，报错原文回灌给它继续改；连续两次同样的失败会提示它换思路，连续三次就停下交给人（`stuck`），不再空烧 token。仓库里没有可跑的测试时，它不会假装完成，而是明确告诉你「无法验证」。
4. **留轨迹**：每一步（读了什么、改了什么、测试输出是什么）都写进 `.mend/runs/*.jsonl`，可以完整回放。

## 30 秒跑起来

```bash
git clone https://github.com/wanrenhuifu/mend && cd mend
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

跑一次是实时逐行往下滚的（每步一行），跑完给报告。想回看就 `mend trace`——下面是接 `deepseek-chat` 跑评测任务 `off_by_one` 的真实记录：

```
运行  eval-off_by_one-234351
任务  calc.py 里的 average() 算错了：请修好它，不要修改测试。
仓库  D:\Code
开始  2026-09-12 23:43:51
结果  done  测试通过  有改动  步数 4  用时 9.1s

#0  ok   context repo_map     +  0.1s      0ms  候选文件: test_calc.py, calc.py
#1  ok   llm     step1        +  2.0s   1893ms  -> read_file, read_file
#2  ok   tool    read_file    +  2.0s      1ms  calc.py 共 13 行，显示 1-13：
#3  ok   tool    read_file    +  2.0s      1ms  test_calc.py 共 13 行，显示 1-13：
#4  ok   llm     step2        +  3.6s   1688ms  -> edit_file
#5  ok   tool    edit_file    +  3.7s      7ms  已修改 calc.py
#6  ok   llm     step3        +  5.3s   1634ms  -> run_command
#7  ok   tool    run_command  +  5.8s    459ms  exit=0 (456ms)
#8  ok   llm     step4        +  7.4s   1656ms  改了什么：calc.py 第 7 行 `for number in numbers[:-1]:` 改为 `for number in numbers:`。 为什么...
#9  ok   tool    run_command  +  7.9s    490ms  exit=0 (487ms)
#10 ok   verify  python -m pytest -q +  7.9s    490ms  通过 3 passed in 0.04s
#11 ok   stop    done         +  7.9s      0ms  验证=通过  改动=有
#12 ok   judge   expect=pass  +  8.4s      0ms  通过
```

前三列是序号 / 成败 / 类型（`context` 组装上下文、`llm` 模型决策、`tool` 工具调用、`verify` 验证门、`judge` 评测判定），后面是相对开始的时刻、耗时和一句话摘要。这次只花了 4 步：并行读两个文件、一次精确替换、自己跑一遍测试，然后交活（第 10 行是验证门再跑一次，第 12 行是评测框架的最终判定）。

失败的那一步会把报错末尾几行直接打出来（省略号表示中间截断了）：

```
#6  FAIL tool    run_command  +  0.6s    546ms  exit=1 (544ms)
      ...
      FAILED test_calc.py::test_average_of_three - assert 1.0 == 2
      2 failed, 1 passed in 0.09s
```

你不用点开任何文件就知道它为什么没通过。

输出层的细节：颜色只在 TTY 下输出（管道和 CI 日志里是纯文本），工具名按权限级别上色（read 蓝 / write 黄 / shell 紫）；想控制可以用 `--color always|never`，或者按惯例设 `NO_COLOR` / `FORCE_COLOR`。报告里的"改动"只包含**这次运行改过的文件**，你自己手改的东西不会被算成它的产出。

前三列是序号 / 成败 / 类型（`context` 组装上下文、`llm` 模型决策、`tool` 工具调用、`verify` 验证门、`judge` 评测判定），后面是相对开始的时刻、耗时和一句话摘要；失败的那一步会额外把报错末尾几行打出来。

输出层的细节：颜色只在 TTY 下输出（管道和 CI 日志里是纯文本），工具名按权限级别上色（read 蓝 / write 黄 / shell 紫）；想要控制可以用 `--color always|never`，或者按惯例设 `NO_COLOR` / `FORCE_COLOR`。报告里的"改动"只包含**这次运行改过的文件**，你自己手改的东西不会被算成它的产出。

## 设计取舍（面试会问的六个问题）

**1. 为什么不用 LangChain？**
核心只有三件事：组装上下文、调度工具、判断什么时候算完。自己写（这个仓库 1500 行左右）才能在面试里讲清每一步的失败模式，也才能让别人 30 秒复现。框架替你省掉的部分，往往正是 agent 最难的部分。

**2. 怎么防止「改测试让测试变绿」？**
两层：提示词里把话说清楚（认为测试写错了就在报告里说明理由，别迎合它），以及**工具层的结构保证**——测试文件（`protected_paths`）写工具根本碰不到，被拒绝的尝试还会留在轨迹里等着被 review。实测观察：给模型一个"断言本身写错了"的仓库，它自己就交了一份「没有改动任何文件，问题出在测试」的报告，压根没试过改测试。

**3. 只读模式怎么保证？**
不是靠提示词求模型别乱改，而是工具分级挂载：`plan` / `review` 只挂 read 级工具，写工具既不在模型的 schema 里，真调了也会被 `registry.call()` 挡掉。

**4. 上下文怎么选？**
先给地图（每个文件多少行），再按任务关键词打分初筛（英文按单词切，中文用 2-gram，因为没有词边界），读文件默认只给 400 行。上下文长度 = 成本 = 延迟，也是 agent 最常见的失败来源。

**5. 为什么零依赖？**
标准库够用：`urllib` 调 API、`argparse` 做 CLI、`tomllib` 读配置。面试官 `git clone` 之后不建虚拟环境就能跑，这比多几个漂亮的依赖值钱。

**6. 命令执行的安全边界在哪？**
三层，但先说清它**不是沙箱**：①命令白名单（只放测试和 linter 这类工具）；②路径检查——白名单只管第一段命令名，`python -c "open('.env').read()"` 这种东西得单独挡；③子进程的环境变量里**不含 API key**（不是靠字符串匹配挡住读取，而是根本不传给它）。真要隔离得把命令放进容器，那是 Roadmap 里的下一步，不是现在的实现。

## 评测

```bash
python -m mend eval --fake      # 离线：用任务自带剧本验证整条流水线
python -m mend eval --json      # 接上真实模型后：输出可统计的结果
```

一个任务 = 一个坏仓库（`evals/fixtures/`）+ 一句任务描述 + 一条判定命令。评测时 fixture 会被复制到临时目录并 `git init`，**你的原仓库全程不动**。目前带了一个示例任务 `off_by_one`，接 `deepseek-chat` 跑的结果是 1/1、4 步、约 8 秒。任务集还很小，扩大评测集在 Roadmap 里。

离线剧本放在 `evals/scripts/`，**不在任务工作区里**——最早它和坏仓库放在一起，结果真实模型会在第一步读到答案（见 [docs/design.md](docs/design.md) 的踩坑记录）。

**加一个新任务**需要三样东西：

1. `evals/fixtures/<名字>/`：一个故意留了 bug 的小仓库（代码 + 现在是红的测试）
2. `evals/tasks/<名字>.toml`：任务描述 + fixture 路径 + 判定命令（照抄 `off_by_one.toml` 改四行）
3. `evals/scripts/<名字>.json`（可选）：离线剧本。没有剧本的任务在 `--fake` 模式下会被跳过，这样加任务不会顺手把 CI 弄红

选任务的时候别选"同一个 bug 换个数字"，要覆盖不同的失败类型：边界条件、语言语义陷阱、状态与缓存、并发。

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

- [x] 测试文件不可写（结构上防止改测试作弊）
- [x] CI：ruff + pytest + 离线 `mend eval --fake`（[ci.yml](.github/workflows/ci.yml)）
- [ ] 多文件改动 + 可回滚的事务
- [ ] 用 tree-sitter 做符号索引，替代关键词初筛
- [ ] 评测集扩到 10+ 个任务，输出成功率 / 步数 / 成本曲线
- [ ] 命令放进容器里跑，把"纵深防御"换成真隔离

## License

MIT
