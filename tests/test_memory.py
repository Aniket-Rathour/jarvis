"""Memory tests on a throwaway database (never the real ~/.local/share/jarvis/memory.db)."""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["JARVIS_MEMORY_DB"] = str(Path(tempfile.mkdtemp()) / "memory.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory as m  # noqa: E402

assert m.PATH != Path.home() / ".local/share/jarvis/memory.db"

# notes
assert m.remember("My sister Priya's birthday is on 14 November.") == "remembered"
assert m.remember("my sister priya's birthday is on 14 november.") == "already remembered"
assert "Priya" in m.recall("Priya birthday")
assert m.recall("what's my favourite colour").startswith("nothing in memory")
assert m.forget("Priya").startswith("forgot 1")

# to-dos + due dates
assert m.parse_due("blah") == ""
assert m.parse_due("tomorrow") > m.parse_due("today")
m.todo_add(["call Rahul by friday", "buy milk"])
m.todo_add(["submit report"], "notion", "today")
rows = {r["text"]: r for r in m.todo_rows()}
assert "call Rahul" in rows and rows["call Rahul"]["due"], "the 'by friday' phrase becomes a due date"
assert "submit report" in m.todo_list(when="today") and "buy milk" not in m.todo_list(when="today")
assert m.todo_done("milk").startswith("marked done")
assert "buy milk" not in m.todo_list()
assert m.todo_done("milk", undo=True).startswith("reopened")

# job results kept whole
rid = m.save_job("research", "Research: keyboards", "Top picks: Keychron K2 and more. " * 50)
assert "Keychron" in m.get_research(rid=rid) and "Keychron" in m.get_research("keyboards")

# old conversation folds into daily digests and stays searchable
db = m._conn()
db.execute("INSERT INTO turns(role,text,ts) VALUES ('user','remind me about the DSA contest',?)", (time.time() - 60 * 86400,))
db.commit()
assert "folded 1" in m.tidy(force=True)
assert "DSA contest" in m.recall("DSA contest")
assert "dynamic" in m.stats()
assert "His to-do list" in m.prompt_block()
print("memory: all tests passed")
