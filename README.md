# IP-MCP

特許庁「特許情報取得API」(`https://ip-data.jpo.go.jp`) を MCP サーバーとして公開し、Claude Desktop / Claude Code から自然言語で特許情報を引けるようにする。

LAN 内デプロイ専用 (`<DEPLOY_HOST>:8765`)。Python 3.12 + FastMCP + httpx、Docker Compose で常駐。

## ドキュメント

- **[PLAN.md](PLAN.md)** — 設計計画書（アーキテクチャ・全ツール一覧・段階計画）
- **[CLAUDE.md](CLAUDE.md)** — Claude Code 向けの操作ガイド（譲れない設計規則・JPO API の罠）

## プレースホルダの読み替え

このリポジトリは Public のため、デプロイ先の LAN IP・SSH ユーザー名はプレースホルダ化してあります。pull した人は自分の環境に合わせて置き換えてください。

| プレースホルダ | 例 | 設定方法 |
|---|---|---|
| `<DEPLOY_HOST>` | `192.0.2.10` | デプロイ先サーバーの LAN IP |
| `<SSH_USER>` | `alice` | サーバーの SSH ユーザー名 |

`docker-compose.yml` のポートバインドはデフォルト `127.0.0.1:8765` (= 同マシンからのみ)。LAN 公開する場合は `docker-compose.override.yml` を別途作成 (`.gitignore` 済) して上書きしてください。例:

```yaml
# docker-compose.override.yml (commit しない)
services:
  ip-mcp:
    ports:
      - "192.0.2.10:8765:8765"   # 自分の LAN IP に置き換え
```

## クイックスタート

### ローカル開発

```bash
cp .env.example .env          # JPO_USERNAME / JPO_PASSWORD を記入
chmod 600 .env
docker compose up -d --build
curl http://127.0.0.1:8765/healthz
```

### デプロイ (<DEPLOY_HOST>)

```bash
ssh <SSH_USER>@<DEPLOY_HOST> "mkdir -p ~/ip-mcp"
git clone https://github.com/kitepon-rgb/IP-MCP.git ~/ip-mcp     # 初回のみ
ssh <SSH_USER>@<DEPLOY_HOST> "cd ~/ip-mcp && git pull && docker compose up -d --build"
```

### Claude Desktop / Code 接続

```json
{
  "mcpServers": {
    "ip-mcp": {
      "transport": { "type": "sse", "url": "http://<DEPLOY_HOST>:8765/sse" }
    }
  }
}
```

## 設計上の重要ルール

- 公式 JPO API ツール (`jpo_*`) と外部検索ツール (`external_*`) は完全分離。**自動フォールバックしない**。
- 詳細は [PLAN.md §2.5](PLAN.md) と [CLAUDE.md](CLAUDE.md) を参照。

## ライセンス

MIT
