# IP-MCP 運用手順書

デプロイ済みサーバー (`<DEPLOY_HOST>`) に対する日常的な運用作業をまとめる。

---

## アクセスログとクォータ確認

すべての JPO 公式 API コール・外部キーワード検索コールは `logs/access.jsonl` に 1 行ずつ JSONL で記録される。スキーマは [src/ip_mcp/access_log.py](src/ip_mcp/access_log.py) を参照。

### 直近 24 時間の集計

```bash
ssh <SSH_USER>@<DEPLOY_HOST> "cd ~/ip-mcp && uv run python scripts/summarize_logs.py --days 1"
```

出力例:

```
=== access log summary - last 1 day(s) ===
window starts: 2026-04-30T16:00:00+00:00
total calls:   42

by source:
      40  jpo_official
       2  google_patents_unofficial

by outcome:
      40  ok
       2  not_found

top 20 endpoints:
     12  avg    180 ms  /api/patent/v1/app_progress/...  remain=788
      8  avg    150 ms  /api/patent/v1/case_number_reference/...  remain=792
      ...

latest JPO remainAccessCount per endpoint:
    788  /api/patent/v1/app_progress/...
    792  /api/patent/v1/case_number_reference/...
```

### 週次レビュー

`--days 7` で 1 週間、`--days 30` で 1 ヶ月分を集計。`remain=...` 列で各エンドポイントの最新残量が確認できる。日次上限が 30〜800/日（エンドポイント別）なので、ここが小さくなっていたらクォータ消費過多のサイン。

### ログのローテーション

`access.jsonl` は append-only。サイズが大きくなったら手動でローテーションする:

```bash
ssh <SSH_USER>@<DEPLOY_HOST> "cd ~/ip-mcp/logs && mv access.jsonl access-$(date +%Y%m).jsonl && touch access.jsonl"
```

コンテナは line-buffered で `access.jsonl` への参照を保持しているので、`mv` 後に新しい空ファイルを `touch` するだけで OK（再起動不要）。月次で運用するのが目安。

---

## OAuth マスターパスワードの変更（rotate）

**前提**: 現在の構成では、デプロイホスト上の `.env`（`MCP_OAUTH_MASTER_PASSWORD`）にマスターパスワードを置いている。これを変更すると、認可ページで入力するパスワードが切り替わる。

### 影響範囲

- 既に発行済みのアクセス・リフレッシュトークンは **影響を受けない**（SQLite に保存され、トークン自体は引き続き有効）
- ただし **新しい認可フローを通すとき** にだけ新パスワードが必要になる
- iPhone Claude / claude.ai 側の Custom Connector 接続は、既存トークンが期限切れになるまで動き続ける（リフレッシュトークンも含めて 30 日）
- 30 日後または明示的にトークンを失効させたタイミングで、クライアント側で再認可フローが走り、新パスワードの入力を求められる

### 手順

1. 新パスワードを生成（24 文字以上推奨）:

   ```bash
   openssl rand -base64 32
   ```

2. デプロイホストの `.env` を更新:

   ```bash
   ssh <SSH_USER>@<DEPLOY_HOST>
   cd ~/ip-mcp
   nano .env       # MCP_OAUTH_MASTER_PASSWORD=<新パスワード> に書き換え
   chmod 600 .env  # 念のため
   ```

3. コンテナ再起動（コードは変えていないので `--build` 不要）:

   ```bash
   docker compose restart ip-mcp
   docker compose logs -f ip-mcp  # 起動ログ確認
   ```

4. 動作確認: 新規クライアントを 1 つ接続して認可フローを通し、新パスワードで通ることを確認。

### 既存トークンを即時失効させたい場合

漏洩等で「今すぐ全クライアントを切りたい」場合は、SQLite ファイルを削除すればよい:

```bash
ssh <SSH_USER>@<DEPLOY_HOST> "cd ~/ip-mcp && docker compose stop ip-mcp && rm -f data/oauth.db data/oauth.db-shm data/oauth.db-wal && docker compose start ip-mcp"
```

注意:
- 全クライアントが再認可必須になる（iPhone Claude / claude.ai でアプリ側からの再追加が必要）
- DCR で登録されたクライアント定義も全部消える（OAuth クライアント ID は再発行）

---

## デプロイ更新

通常更新（`git pull` + ビルド + 再起動）:

```bash
ssh <SSH_USER>@<DEPLOY_HOST> "cd ~/ip-mcp && git pull && docker compose up -d --build"
```

依存関係に変更がない場合（コード変更だけ）は `--build` を省略して `docker compose restart ip-mcp` でも可。

---

## トラブルシューティング

### iPhone Claude から繋がらない

1. コンテナのヘルスチェック: `docker compose ps` で `healthy` か確認
2. Caddy のログ: `sudo journalctl -u caddy -n 50`
3. OAuth エンドポイント疎通: `curl https://ipmcp.<domain>.dynv6.net/.well-known/oauth-authorization-server`
4. アクセスログ: `tail logs/access.jsonl` で直近のコールを確認

### 「rate_limited_daily」が出始めた

`scripts/summarize_logs.py --days 1` で消費量を確認。JPO 日次上限 (`statusCode 203`) は当日 0 時にリセットされる。翌日 0 時を過ぎても回復しない場合はログイン認証情報を疑う（`scripts/token_check.py` で確認）。

### LAN 内 PC から `https://ipmcp.<domain>.dynv6.net` で繋がらない

ヘアピン NAT が無効。Windows の `C:\Windows\System32\drivers\etc\hosts` に LAN 直結エントリを追加（管理者権限が必要）:

```
<DEPLOY_HOST>    ipmcp.<domain>.dynv6.net
```

その後 `ipconfig /flushdns` で DNS キャッシュをクリア。
