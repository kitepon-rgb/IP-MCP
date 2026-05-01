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

### JPO API レート制約とクォータ

JPO 公式 API は自主制御責任を運用者に課している。違反した場合は `statusCode 303`（一時的な高負荷）または `statusCode 203`（日次上限）が返り、当該エンドポイントは一時的または当日中アクセス不能になる。

**分次レート（自主制御）**:

- `/api/patent/*` 系: **10 req/min**
- `/opdapi/*` 系: **5 req/min**（OPD は別系統で別カウント）

クライアント側のスライディングウィンドウで制御する。違反検知は `statusCode 303` で再試行可（指数バックオフ、同一エンドポイント内のみ）。

**日次クォータ**:

エンドポイントごとに 30〜800/日の幅で異なる（2026 年 3 月から国内系は 2 倍緩和済）。具体値は JPO 仕様書に数値として明記されておらず、各レスポンスの `result.remainAccessCount` が実数の信頼ソース。`scripts/summarize_logs.py` の `latest JPO remainAccessCount per endpoint` 列で確認できる。

**ツール → エンドポイント マッピング**:

| ツール | エンドポイント | 分次 | 備考 |
|---|---|---|---|
| `jpo_convert_patent_number` | `/api/patent/v1/case_number_reference/...` | 10/min | |
| `jpo_get_patent_progress` | `/api/patent/v1/app_progress/...` | 10/min | |
| `jpo_get_patent_registration` | `/api/patent/v1/registration_info/...` | 10/min | |
| `jpo_get_patent_citations` | `/api/patent/v1/cite_doc_info/...` | 10/min | |
| `jpo_get_divisional_apps` | `/api/patent/v1/divisional_app_info/...` | 10/min | |
| `jpo_get_priority_apps` | `/api/patent/v1/priority_right_app_info/...` | 10/min | |
| `jpo_lookup_applicant` | `/api/patent/v1/applicant_attorney[_cd]/...` | 10/min | 完全一致のみ |
| `jpo_get_patent_documents` | `/api/patent/v1/app_doc_cont_*/...` | 10/min | binary ZIP / signed URL |
| `jpo_get_jpp_url` | （J-PlatPat 静的 URL 生成、API を呼ばない） | — | クォータ消費なし |
| `jpo_get_opd_family` | `/opdapi/v1/family/...` | **5/min** | 別系統 |
| `jpo_get_opd_doc_list` | `/opdapi/v1/global_doc_list/...` | **5/min** | 別系統 |
| `jpo_fetch_full_record` | 上の 4 エンドポイント（`case_number_reference` + `app_progress` + `registration_info` + `cite_doc_info`）を**並列**で叩く | 10/min × 4 並列 | **1 コール = 4 つの別クォータから 1 ずつ消費**。ボトルネックは最低クォータのエンドポイント |

**`jpo_fetch_full_record` の運用上の注意**:

- 1 コール = 4 つのエンドポイントの日次クォータをそれぞれ 1 ずつ消費する（同一クォータから 4 ではない）。
- 「N 件の特許をまとめて引きたい」要求は、ツールを N 回呼ぶ = 4×N 回の API コールを発生させる。当日のクォータ消費量を見誤りやすい。
- 残量が小さいエンドポイントが当日中に枯渇する可能性がある。連続実行する前に `scripts/summarize_logs.py --days 1` で各エンドポイントの最新 `remain=` を確認するのが安全。経験則として「いずれかが 200 以下に落ちたら `fetch_full_record` を控える」が安全側。

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

### ツール検証ステータス (2026-05-01)

実装済 13 ツールの動作確認結果。本番デプロイ済 (`5402756`) に対して `特開2010-228687`（出願 `2009080841` / 株式会社日立製作所）で実 JPO API を叩いた結果。

| # | ツール | 検証 | 経路 | 備考 |
|---|---|---|---|---|
| 1 | jpo_convert_patent_number | ✅ | MCP | 番号 3 種を正しく変換 |
| 2 | jpo_get_patent_progress | ✅ | MCP | simple=true で priority/divisional 省略確認 |
| 3 | jpo_get_patent_registration | ✅ | MCP | 権利存続中・満了日・年金状況返却 |
| 4 | jpo_get_patent_citations | ✅ | MCP | 引用 20 件 (検索報告書 + 拒絶理由) |
| 5 | jpo_get_divisional_apps | ✅ | MCP | 該当なし → 空配列で正常応答 |
| 6 | jpo_get_priority_apps | ✅ | MCP | 同上 |
| 7 | jpo_lookup_applicant | ✅ | MCP | コード → 氏名変換 |
| 8 | jpo_get_jpp_url | ✅ | MCP | J-PlatPat 固定 URL 生成 |
| 9 | jpo_get_patent_documents | ✅ | container exec | バグ修正後、binary ZIP 1789 バイトを正しく取得 (PK\x03\x04 マジック確認)。MCP 経由の最終確認は OAuth 再認可後に再テスト要 |
| 10 | jpo_get_opd_family | 🟡 | MCP | `rate_limited_daily` (今日のクォータ枯渇)。エラー応答は構造正常、自動フォールバックなし。**翌日再テスト必要** |
| 11 | jpo_get_opd_doc_list | 🟡 | MCP | 同上、**翌日再テスト必要** |
| 12 | jpo_fetch_full_record | ✅ | MCP | 4 サブコール並列、各 statusCode 100 |
| 13 | external_search_patents_by_keyword | ✅ | MCP | 12057 件ヒット、`source: google_patents_unofficial` |

**未チェック / 保留事項**:
- 🔄 jpo_get_patent_documents の MCP 経由 end-to-end は claude.ai の OAuth 再認可後に未実施。container 内 `JpoClient.get_raw()` 直叩きでは確認済
- 🔄 OPD 2 ツール (`#10, #11`) は今日のクォータが枯渇していたため `rate_limited_daily` の構造化エラー応答までしか確認できず。実データ取得経路の動作は翌日（クォータリセット後）に再テスト必要
- 🔄 OAuth SQLite 永続化の「再起動後もトークン生存」は、今回のデプロイがメモリ持ちトークンの最後の消失イベント（旧コード→新コードへの切替時）。次回以降の再起動・再ビルドでトークン生存を確認できる

### LAN 内 PC から `https://ipmcp.<domain>.dynv6.net` で繋がらない

ヘアピン NAT が無効。Windows の `C:\Windows\System32\drivers\etc\hosts` に LAN 直結エントリを追加（管理者権限が必要）:

```
<DEPLOY_HOST>    ipmcp.<domain>.dynv6.net
```

その後 `ipconfig /flushdns` で DNS キャッシュをクリア。
