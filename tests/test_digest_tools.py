import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.schemas import DigestItemOut, DigestOut
from app.db.models import Base, Subscription, User
from app.services import delivery_service, job_runner
from app.tools import digest_tools, registry


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'digest_tools.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(digest_tools, "AsyncSessionLocal", maker)
    yield maker
    await engine.dispose()


async def _seed(maker, with_sub=True):
    async with maker() as s:
        user = User(name="Tester", email="t@example.com", timezone="Asia/Shanghai")
        s.add(user)
        await s.flush()
        if with_sub:
            s.add(Subscription(user_id=user.id, topics="大模型", keywords="GPT", delivery_time="07:30"))
        await s.commit()


def _fake_runtime(monkeypatch):
    class FakeRuntime:
        async def run(self, request, audit=None):
            item = DigestItemOut(title="GPT-5 发布", url="https://x.com/g", summary="摘要", reason="相关")
            return type("R", (), {
                "steps": 1, "tool_calls": 1, "output_text": "",
                "digest": DigestOut(title="今日AI", overview="概述", items=[item]),
            })()

    monkeypatch.setattr(job_runner, "AgentRuntime", lambda: FakeRuntime())
    monkeypatch.setattr(delivery_service.settings, "smtp_host", "")


def test_digest_tools_are_registered():
    names = {s["function"]["name"] for s in registry.get_schemas()}
    assert {"generate_digest", "list_digests", "get_digest", "get_settings"} <= names
    for name in ("generate_digest", "list_digests", "get_digest", "get_settings"):
        assert name in registry._HANDLERS


@pytest.mark.asyncio
async def test_generate_then_list_then_get(factory, monkeypatch):
    await _seed(factory)
    _fake_runtime(monkeypatch)

    made = await digest_tools.generate_digest()
    assert made["ok"] is True
    assert made["digest"]["title"] == "今日AI"
    assert made["digest"]["items"][0]["title"] == "GPT-5 发布"

    listed = await digest_tools.list_digests()
    assert listed["ok"] is True and listed["count"] == 1
    digest_id = listed["digests"][0]["id"]

    detail = await digest_tools.get_digest(digest_id)
    assert detail["ok"] is True
    assert detail["digest"]["overview"] == "概述"


@pytest.mark.asyncio
async def test_generate_requires_subscription(factory):
    await _seed(factory, with_sub=False)
    res = await digest_tools.generate_digest()
    assert res["ok"] is False
    assert "订阅" in res["error"]


@pytest.mark.asyncio
async def test_get_digest_missing(factory):
    await _seed(factory)
    res = await digest_tools.get_digest(999)
    assert res["ok"] is False


@pytest.mark.asyncio
async def test_get_settings_reports_profile_and_subscription(factory):
    await _seed(factory)
    res = await digest_tools.get_settings()
    assert res["ok"] is True
    assert res["profile"]["timezone"] == "Asia/Shanghai"
    assert res["subscription"]["delivery_time"] == "07:30"
    assert res["subscription"]["topics"] == ["大模型"]
