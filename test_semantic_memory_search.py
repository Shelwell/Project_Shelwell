from backend.memory.semantic import SemanticStore

# s = SemanticStore()
# r = s.list_facts(page=1, page_size=200)
# print("total", r["total"])
# for f in r["items"]:
#     print(f.id, "|", f.state, "|", f.subject, "|", f.fact)


fid = "sem_2d1335a1cd18"
s = SemanticStore()
ok = s.update(fid, {
    "subject": "[佩琪]",
    "fact": "[佩琪]计划赚钱后给[西尔维娅]升级设备",
    "state": "active",
})
print("ok", ok)