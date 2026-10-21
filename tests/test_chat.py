import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.agent.runtime import AgentRuntime
from app.db.models import Base, ChatMessage, User
from app.tools import registry


# ---------- fake LLM plumbing ----------
class FakeToolCall:
    def __init__(self, name, args, call_id="call_1"):
        self.id = call_id
        self.function = type("F", (), {"name": name, "arguments": json.dumps(args)})()


class FakeMessage:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeResponse:
    def __init__(self, message):
        self.choices = [type("C", (), {"message": message})()]


class FakeCompletions:
    def __init__(self, script):
        self.script = list(script)

    async def create(self, **kwargs):
        step = self.script.pop(0)
        return step(kwargs) if callable(step) else step


class FakeClient:
    def __init__(self, script):
        self.chat = type("C", (), {"completions": FakeCompletions(script)})()


@pytest.fixture
async def session(tmp_path):
    db_path = tmp_path / "chat_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


# ---------- chat runtime ----------
@pytest.mark.asyncio
async def test_chat_runtime_calls_tool_then_returns_plain_text(monkeypatch):
    async def fake_list_dir(path="."):
        return {"ok": True, "path": path, "entries": [{"name": "a.txt", "type": "file"}]}

    monkeypatch.setitem(registry._HANDLERS, "list_dir", fake_list_dir)

    captured = []

    def step_1(kwargs):
        captured.append(kwargs)
        return FakeResponse(FakeMessage("", [FakeToolCall("list_dir", {"path": "."})]))

    runtime = AgentRuntime(client=FakeClient([step_1, FakeResponse(FakeMessage("workspace 里有 a.txt"))]))
    result = await runtime.run_chat(
        [{"role": "user", "content": "看看 workspace 里有什么"}],
        user_name="测试",
        timezone="Asia/Shanghai",
    )

    assert result.text == "workspace 里有 a.txt"
    assert result.steps == 2
    assert [t["name"] for t in result.tool_calls] == ["list_dir"]
    assert result.tool_calls[0]["ok"] is True
    # system prompt + user message were sent
    assert captured and captured[0]["messages"][0]["role"] == "system"


@pytest.mark.asyncio
async def test_chat_runtime_reports_tool_error_without_crashing(monkeypatch):
    async def broken(command):
        return {"ok": False, "error": "bash tool is disabled"}

    monkeypatch.setitem(registry._HANDLERS, "bash", broken)

    runtime = AgentRuntime(
        client=FakeClient(
            [
                FakeResponse(FakeMessage("", [FakeToolCall("bash", {"command": "ls"})])),
                FakeResponse(FakeMessage("bash 当前被禁用了。")),
            ]
        )
    )
    result = await runtime.run_chat(
        [{"role": "user", "content": "运行 ls"}],
        user_name="测试",
        timezone="Asia/Shanghai",
    )
    assert result.text == "bash 当前被禁用了。"
    assert result.tool_calls[0]["ok"] is False


# ---------- chat API ----------
@pytest.mark.asyncio
async def test_chat_api_persists_user_and_assistant(session, monkeypatch):
    from app.api import chat as chat_api

    session.add(User(name="Tester", email="c@example.com", timezone="Asia/Shanghai"))
    await session.commit()

    class FakeRuntime:
        async def run_chat(self, history, *, user_name, timezone):
            assert history[-1]["content"] == "你好"
            return type("R", (), {
                "text": "你好，需要我做什么？",
                "steps": 1,
                "tool_calls": [],
                "error": "",
            })()

    monkeypatch.setattr(chat_api, "ChatRuntime", lambda: FakeRuntime())

    out = await chat_api.send_chat(chat_api.ChatIn(message="你好"), session)
    assert out.reply == "你好，需要我做什么？"

    rows = (await session.execute(select(ChatMessage).order_by(ChatMessage.id))).scalars().all()
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[0].content == "你好"


@pytest.mark.asyncio
async def test_chat_api_history_returns_messages(session):
    from app.api import chat as chat_api

    user = User(name="Tester", email="h@example.com", timezone="Asia/Shanghai")
    session.add(user)
    await session.flush()
    session.add_all([
        ChatMessage(user_id=user.id, session_id="default", role="user", content="问题一"),
        ChatMessage(user_id=user.id, session_id="default", role="assistant", content="回答一"),
        ChatMessage(user_id=user.id, session_id="other", role="user", content="别的会话"),
    ])
    await session.commit()

    history = await chat_api.get_history(session_id="default", session=session)
    assert [m["content"] for m in history] == ["问题一", "回答一"]
