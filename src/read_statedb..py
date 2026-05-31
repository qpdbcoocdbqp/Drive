from src.module.hermesdb import StateDB


db_path=".hermes/state.db"

with StateDB(db_path=db_path) as db:
    # 1. 使用時段分布（所有 session）
    sessions = db.list_sessions(limit=1000)

print(sessions[0])

session_id = sessions[0].get("id")
print(session_id)

with StateDB(db_path=db_path) as db:
    # 2. 某 session 的完整對話（只看 user/assistant，跳過 tool）
    msgs = db.get_messages(session_id, roles=["user", "assistant"])

print("Sessions:", msgs)

with StateDB(db_path=db_path) as db:
    # 3. 搜尋使用者提過的關鍵字
    hits = db.search_messages("deploy", use_trigram=True)

print(hits[0])

with StateDB(db_path=db_path) as db:
    # 4. 某 session 的 token / cost 摘要
    stats = db.session_stats(session_id)
print(stats)

with StateDB(db_path=db_path) as db:
    # 5. 追溯壓縮鏈（長對話被拆成多個 session）
    chain = db.get_session_chain(session_id)
print(chain)
