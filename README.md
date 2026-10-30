# pxxl-deploy-watch

Pxxl（pxxl.app）免费层部署看门狗。免费用户被锁在 `rgn_eu_central`，该区 CPU 常年打满时部署会一直 `queued`，
这个仓库每 3 小时检查一次，排上了就实测域名并推 TG。

## 它在干什么

1. 用 `PXXL_REFRESH` 刷新 access token（**refresh token 会轮换**，脚本刷新后立刻把新值写回 repo secret）
2. 查 `proj_5el66e524fbqjyro0hrw` 的最新部署状态
3. 部署 `running` → 请求 `https://btpp-test.pxxlspace.cv`，HTTP 2xx 就推 TG 并置变量 `PXXL_DONE=1`（之后自动静默）
4. 部署 `failed` 或排队超过 12 小时 → 调 `POST /projects/{id}/redeploy` 重新排一次
5. 其它情况（还在排队）→ 只写日志，不打扰

## 节奏

- GitHub Actions `schedule`：每 3 小时第 20 分（备用腿，账号原生 schedule 常不 fire）
- **cron-job.org**：每 3 小时调一次 `workflow_dispatch`（主腿）

## Secrets

- `PXXL_REFRESH`：Pxxl 的 refresh token（会被脚本自动更新）
- `PXXL_DEVICE_ID`：设备指纹（换值要重走设备验证，别动）
- `GH_PAT`：用于回写 secret / 设置变量
- `TG_BOT_TOKEN` / `TG_CHAT_ID`：推送用

## 手动触发

```bash
export GH_TOKEN=$(cat /root/.gh_tok)
gh workflow run watch.yml -R btpp03/pxxl-deploy-watch
gh run list -R btpp03/pxxl-deploy-watch -L 3
```

## 停掉

删除仓库即可；只想让它别再发消息，把变量 `PXXL_DONE` 设为 `1`。

## 已知结论（2026-10-30）

- Free 套餐：5 项目（2 Web Service + 3 静态站）、1 域、0 数据库、300 次部署/月、
  单实例 0.5 vCPU / 0.5 GB、账号总算力 1C/2GB、50 GB 带宽、无 cron / 无终端 / 无监控
- 运行时区域仅欧洲（纽伦堡默认、巴黎、赫尔辛基/哥本哈根、弗吉尼亚），无非洲节点
- 免费用户**不能选区域**（`402 plan_upgrade_required`），只能跟着推荐区排队
- 平台是「容器 + 端口」模型，不给 root，不能当 VPS 用
