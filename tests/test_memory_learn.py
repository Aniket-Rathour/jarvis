"""Auto-learning must add only NEW facts and only clear to-dos (real Gemini call, throwaway database).
Run: .venv/bin/python tests/test_memory_learn.py"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["JARVIS_MEMORY_DB"] = str(Path(tempfile.mkdtemp()) / "memory.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
import memory as m  # noqa: E402
from google import genai  # noqa: E402

m.remember("Aryan is a person the user collaborates with on problem statements and planning.", "fact", "auto")
m.remember("The user is participating in the Smart India Hackathon (SIH).", "fact", "auto")
m.todo_add(["Send the SIH PPTs to Aryan"], "conversation")
convo = [("user", "Aryan is my teammate, we plan the problem statements together."), ("assistant", "Noted, sir."),
         ("user", "Open the meet."), ("assistant", "Opening it."),
         ("user", "We are in the Smart India Hackathon, you know that."), ("assistant", "Yes, sir."),
         ("user", "By the way, my sister Priya's birthday is on 14 November."), ("assistant", "I'll remember, sir."),
         ("user", "There's a meeting going on, join it."), ("assistant", "Joining."),
         ("user", "Also I need to renew my passport before December 10."), ("assistant", "Understood."),
         ("user", "And send the PPTs to Aryan like I said."), ("assistant", "Of course."),
         ("user", "Volume down."), ("assistant", "Done.")]
for r, t in convo:
    m.add_turn(r, t)
out = asyncio.run(m.learn(genai.Client(api_key=config.GEMINI_API_KEY), config.BRAIN_MODELS))
notes = [r["text"] for r in m._conn().execute("select text from notes where tags='auto' order by id")][2:]
rows = m.todo_rows()
todos = [r["text"] for r in rows]
print("due dates:", {r["text"]: r["due"] for r in rows})
print(out, "\nnew facts:", notes, "\nto-dos:", todos)
checks = {
    "learned the new fact (Priya's birthday)": any("priya" in n.lower() for n in notes),
    "did not re-learn Aryan or SIH": not any("aryan" in n.lower() or "hackathon" in n.lower() or "sih" in n.lower() for n in notes),
    "added the clear to-do (passport)": any("passport" in t.lower() for t in todos),
    "kept its deadline (10 December)": any("passport" in r["text"].lower() and (r["due"] or "").endswith("-12-10") for r in rows),
    "worded as a task, not 'I need to...'": not any(t.lower().startswith(("i need", "i have")) for t in todos),
    "no vague 'join/attend the meeting' to-do": not any("meeting" in t.lower() for t in todos),
    "did not duplicate 'send the PPTs to Aryan'": sum("ppt" in t.lower() for t in todos) == 1,
}
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
sys.exit(0 if all(checks.values()) else 1)
