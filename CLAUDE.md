# Tomoko Support APP

家事最適化サポートAI。詳細な仕組みは [README.md](README.md) 参照。

## 命名について(混同注意)

- **Omochi** — このアプリのAIエージェント/Slack Botの名前。`scripts/nightly_batch.py` の system prompt(`RECEIPT_SYSTEM` / `HEARING_SYSTEM`)で自己紹介させている。Slack上では `@Omochi`。
- **Tomoko** — このアプリの利用者(家族)の名前。プロジェクト名・`#tomokoの要望` チャンネル名・launchd label(`com.tomoko.kakeibo-nightly`)などファイル/識別子上の命名は人名としてのTomokoに由来し、エージェント名とは無関係。リネームしない。

エージェントの応答・自己言及・新規追加するsystem promptは「Omochi」で統一する。Tomokoさん向けの表記(README内の「Tomokoさんは〜」等)は人名のまま変更しない。
