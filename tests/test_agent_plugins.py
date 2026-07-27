import asyncio

import pytest

from web.backend.api import ai_routes


INSTALLED_LINUX_PLUGINS = [
    "linux.pslist.PsList",
    "linux.sockscan.Sockscan",
    "linux.sockstat.Sockstat",
    "linux.tty_check.tty_check",
    "linux.check_modules.Check_modules",
    "linux.check_syscall.Check_syscall",
    "linux.check_idt.Check_idt",
    "linux.capabilities.Capabilities",
    "linux.hidden_modules.Hidden_modules",
    "linux.proc.Maps",
    "linux.module_extract.ModuleExtract",
]


@pytest.mark.parametrize(
    ("requested", "expected", "injected"),
    [
        ("netscan.NetScan", "linux.sockscan.Sockscan", {}),
        ("netstat.Netstat", "linux.sockstat.Sockstat", {}),
        ("ttycheck.TtyCheck", "linux.tty_check.tty_check", {}),
        ("check_modules.CheckModules", "linux.check_modules.Check_modules", {}),
        ("check_syscall.CheckSyscall", "linux.check_syscall.Check_syscall", {}),
        ("capabilities.Caps", "linux.capabilities.Capabilities", {}),
        ("hidden_modules.CheckHiddenModules", "linux.hidden_modules.Hidden_modules", {}),
        ("proc_maps.Maps", "linux.proc.Maps", {}),
        ("threads.Threads", "linux.pslist.PsList", {"threads": True}),
    ],
)
def test_resolve_agent_plugin_names_against_installed_catalog(
    requested,
    expected,
    injected,
):
    resolved, alias_args, _resolution = ai_routes._resolve_agent_plugin_name(
        requested,
        INSTALLED_LINUX_PLUGINS,
        "linux",
    )

    assert resolved == expected
    assert alias_args == injected


def test_resolve_agent_plugin_rejects_uninstalled_name_with_suggestions():
    with pytest.raises(ValueError, match="未安装"):
        ai_routes._resolve_agent_plugin_name(
            "cgroup.Cgroup",
            INSTALLED_LINUX_PLUGINS,
            "linux",
        )


def test_agent_plugin_args_follow_installed_metadata_and_pid_pluralization():
    metadata = {
        "args": [
            {"name": "pids", "arg_type": "string", "required": False},
            {"name": "files_only", "arg_type": "bool", "required": False},
            {"name": "dump", "arg_type": "bool", "required": False},
        ]
    }

    assert ai_routes._extract_agent_plugin_args(
        {"plugin_name": "lsof.Lsof", "pid": 343, "args": {"files-only": "true"}},
        metadata,
    ) == {"pids": "343", "files_only": True}


def test_execute_agent_plugin_uses_dynamic_catalog_and_safe_metadata(monkeypatch):
    categories = {
        "进程相关": ["pslist.PsList"],
        "网络相关": ["sockscan.Sockscan", "sockstat.Sockstat"],
        "安全检查": [
            "tty_check.tty_check",
            "check_modules.Check_modules",
            "check_syscall.Check_syscall",
            "check_idt.Check_idt",
        ],
        "提取": ["module_extract.ModuleExtract"],
    }
    metadata = {
        "linux.pslist.PsList": {
            "args": [
                {"name": "pid", "arg_type": "string", "required": False},
                {"name": "threads", "arg_type": "bool", "required": False},
                {"name": "dump", "arg_type": "bool", "required": False},
            ],
        },
        "linux.module_extract.ModuleExtract": {
            "args": [
                {"name": "base", "arg_type": "int", "required": True},
            ],
        },
    }
    calls = []

    class FakeEngine:
        def get_plugin_metadata(self, plugin_name):
            return metadata.get(plugin_name, {"args": []})

    class FakeManager:
        def list_plugins(self, engine_id, os_family):
            assert engine_id == "vol3"
            assert os_family == "linux"
            return categories

        def get_engine(self, engine_id):
            assert engine_id == "vol3"
            return FakeEngine()

        def run_plugin(self, engine_id, plugin_name, **kwargs):
            calls.append((engine_id, plugin_name, kwargs))
            return ["PID"], [(343,)]

    class FakeService:
        _manager = FakeManager()

    monkeypatch.setattr(ai_routes, "get_service", lambda: FakeService())

    async def run_checks():
        # This plugin was not present in the former static allowlist, but is
        # installed and read-only, so it must now execute.
        check_idt = await ai_routes._execute_agent_tool(
            "run_plugin",
            {"plugin_name": "check_idt.CheckIdt"},
            "vol3",
            os_family="linux",
        )
        threads = await ai_routes._execute_agent_tool(
            "run_plugin",
            {"plugin_name": "threads.Threads", "pid": 343},
            "vol3",
            os_family="linux",
        )
        catalogue = await ai_routes._execute_agent_tool(
            "list_plugins",
            {},
            "vol3",
            os_family="linux",
        )
        return check_idt, threads, catalogue

    check_idt, threads, catalogue = asyncio.run(run_checks())

    assert check_idt["plugin"] == "linux.check_idt.Check_idt"
    assert threads["plugin"] == "linux.pslist.PsList"
    assert calls == [
        ("vol3", "linux.check_idt.Check_idt", {}),
        ("vol3", "linux.pslist.PsList", {"pid": 343, "threads": True}),
    ]
    listed_names = {item["full_name"] for item in catalogue["plugins"]}
    assert "linux.check_idt.Check_idt" in listed_names
    assert "linux.module_extract.ModuleExtract" not in listed_names
    assert catalogue["blocked_plugins"] == [
        {
            "plugin_name": "module_extract.ModuleExtract",
            "reason": "该插件会提取文件，必须通过专用 dump 工具执行",
        }
    ]
