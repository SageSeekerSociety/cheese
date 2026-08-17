"""操作请求清单 — registry 推导、清单校验、PR 文件范围守卫。

这些测试盯的是这套格式赖以成立的几条规矩，而不是它的实现：
registry 是权威（作者改不动风险描述）、不许有隐含的「当前」目标、
envelope 只装 blast_radius=none、以及一个 ops PR 只能碰 ops/requests/。
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
import yaml

import app.domain.ops.services as ops_services
from app.domain.ops.guard import guard_issues, is_manifest_path, is_ops_pr
from app.domain.ops.manifest import (
    ManifestError,
    implicit_target_issues,
    render_manifest,
    validate_manifest,
)
from app.domain.ops.registry import (
    BlastRadius,
    UnknownOperationError,
    describe_registry,
    get_operation,
)
from app.domain.ops.schemas import OperationCardFace

SHA = "9f4c1d0b7a3e5628cf10b4d92a7e6531c08fa2b4"
OTHER_SHA = "0123456789abcdef0123456789abcdef01234567"


def _author(**overrides) -> dict:
    """A well-formed author-written manifest (no `resolved:` — registry renders it)."""
    doc = {
        "schema_version": 1,
        "operation_id": "deploy.dev",
        "topic_id": "b2bcbe11",
        "requested_by": "andy",
        "reason": "dev box 落后 main 三个提交，联调前先对齐",
        "authorization": "once",
        "args": {"commit_sha": SHA},
    }
    doc.update(overrides)
    return doc


def _text(**overrides) -> str:
    return yaml.safe_dump(_author(**overrides), sort_keys=False, allow_unicode=True)


def _path(doc: dict | None = None) -> str:
    doc = doc or _author()
    return f"ops/requests/{doc['topic_id']}-{doc['operation_id']}.yaml"


def _rendered(**overrides) -> tuple[str, str]:
    """(path, fully rendered manifest) for the given author fields."""
    doc = _author(**overrides)
    path = _path(doc)
    return path, render_manifest(path, yaml.safe_dump(doc, allow_unicode=True))


def _issues(path: str, text: str) -> list[str]:
    with pytest.raises(ManifestError) as exc:
        validate_manifest(path, text)
    return exc.value.issues


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_registry_describes_every_operation_with_an_args_schema():
    entries = describe_registry()
    ids = {e["operation_id"] for e in entries}
    assert {"device.smoke", "deploy.dev", "deploy.prod", "backup.restore_test"} <= ids
    for entry in entries:
        assert entry["workflow"].startswith(".github/workflows/")
        assert entry["args_schema"]["properties"]


def test_unknown_operation_lists_what_is_available():
    with pytest.raises(UnknownOperationError) as exc:
        get_operation("deploy.everything")
    assert "deploy.dev" in str(exc.value)


def test_accept_flag_moves_device_smoke_from_read_only_to_landing():
    """`accept: true` 把冒烟从只读探针变成会往上游 trunk 落东西的写操作。
    这一位翻转必须体现在卡面上，否则人看到的还是「只读」。"""
    smoke = get_operation("device.smoke")
    args = {"commit_sha": SHA, "project_id": "p1", "user_handle": "andy"}

    probe = smoke.resolve({**args, "accept": False})
    landing = smoke.resolve({**args, "accept": True})

    assert probe.blast_radius is BlastRadius.none
    assert landing.blast_radius is BlastRadius.dev
    assert probe.blast_radius.envelope_eligible
    assert not landing.blast_radius.envelope_eligible
    assert probe.reversal != landing.reversal
    assert probe.worst_case != landing.worst_case


def test_prod_deploy_demands_two_humans():
    resolved = get_operation("deploy.prod").resolve({"release_tag": "v0.16.4"})
    assert resolved.blast_radius is BlastRadius.prod
    assert resolved.human_approvals_required == 2
    assert not resolved.interruptible


def test_pinned_args_reject_a_moving_target_by_type():
    """PinnedSha / PinnedVersion 是第一道防线：分支名根本满足不了这个类型。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        get_operation("deploy.dev").resolve_args({"commit_sha": "9f4c1d0"})
    with pytest.raises(ValidationError):
        get_operation("deploy.prod").resolve_args({"release_tag": "latest"})


# ---------------------------------------------------------------------------
# 清单校验
# ---------------------------------------------------------------------------


def test_rendered_manifest_validates_and_is_idempotent():
    path, text = _rendered()
    validated = validate_manifest(path, text)

    assert validated.operation_id == "deploy.dev"
    assert validated.resolved.blast_radius is BlastRadius.dev
    assert render_manifest(path, text) == text


def test_render_inlines_the_pinned_arg_into_the_card_face():
    _, text = _rendered()
    doc = yaml.safe_load(text)
    assert SHA[:12] in doc["resolved"]["what"]
    assert doc["args"]["commit_sha"] == SHA


def test_missing_resolved_section_points_at_the_generator():
    issues = _issues(_path(), _text())
    assert any("resolved" in i and "render" in i for i in issues)


def test_author_cannot_talk_the_blast_radius_down():
    """registry 是权威。作者把 resolved: 改得好看一点 = 校验红。"""
    path, text = _rendered()
    doc = yaml.safe_load(text)
    doc["resolved"]["blast_radius"] = "none"
    doc["resolved"]["worst_case"] = "没什么影响"
    tampered = yaml.safe_dump(doc, allow_unicode=True)

    issues = _issues(path, tampered)
    assert any("resolved.blast_radius" in i for i in issues)
    assert any("resolved.worst_case" in i for i in issues)


def test_args_change_invalidates_a_previously_rendered_resolved():
    """改了 args 就要重新 render、重新批 —— 旧的 resolved: 不能跟着蒙混过关。"""
    path, text = _rendered()
    doc = yaml.safe_load(text)
    doc["args"]["commit_sha"] = OTHER_SHA
    issues = _issues(path, yaml.safe_dump(doc, allow_unicode=True))
    assert any("resolved.what" in i for i in issues)


def test_filename_must_agree_with_the_body():
    _, text = _rendered()
    assert any(
        "topic8" in i for i in _issues("ops/requests/deadbeef-deploy.dev.yaml", text)
    )
    assert any(
        "operation_id" in i
        for i in _issues("ops/requests/b2bcbe11-deploy.prod.yaml", text)
    )


def test_manifest_must_sit_directly_under_ops_requests():
    _, text = _rendered()
    assert any(
        "ops/requests" in i for i in _issues("ops/b2bcbe11-deploy.dev.yaml", text)
    )
    assert any(
        "子目录" in i
        for i in _issues("ops/requests/andy/b2bcbe11-deploy.dev.yaml", text)
    )


def test_implicit_current_target_is_rejected_with_the_pinned_reason():
    issues = _issues(_path(), _text(args={"commit_sha": "main"}))
    joined = "\n".join(issues)
    assert "隐含" in joined
    # 类型校验同时也拦下了它 —— 两道防线都要报出来，不是只报第一条。
    assert any("commit_sha" in i and "隐含" not in i for i in issues)


@pytest.mark.parametrize("moving", ["latest", "HEAD", "当前", "最新的那个", "@current"])
def test_moving_target_words_are_caught_anywhere_in_args(moving: str):
    assert implicit_target_issues({"project_id": moving})


def test_implicit_target_scan_walks_nested_args():
    issues = implicit_target_issues({"targets": [{"ref": "main"}]})
    assert issues and "args.targets[0].ref" in issues[0]


def test_a_pinned_sha_is_not_mistaken_for_a_moving_target():
    assert implicit_target_issues({"commit_sha": SHA}) == []


def test_envelope_is_only_open_to_zero_blast_radius():
    """拍板 6：有副作用的操作一律一次性 PR、执行完即关。"""
    issues = _issues(_path(), _text(authorization="envelope"))
    assert any("envelope" in i and "blast_radius=none" in i for i in issues)

    path, text = _rendered(
        operation_id="backup.restore_test",
        authorization="envelope",
        args={"commit_sha": SHA},
    )
    assert validate_manifest(path, text).request.authorization == "envelope"


def test_unknown_operation_id_fails_the_manifest():
    doc = _author(operation_id="deploy.everything")
    issues = _issues(_path(doc), yaml.safe_dump(doc, allow_unicode=True))
    assert any("未知 operation_id" in i for i in issues)


def test_unknown_arg_is_rejected_rather_than_ignored():
    """args 是授权的一部分，多出来的字段不能被静默丢掉。"""
    issues = _issues(
        _path(), _text(args={"commit_sha": SHA, "skip_health_check": True})
    )
    assert any("skip_health_check" in i for i in issues)


def test_every_problem_is_reported_in_one_pass():
    doc = _author(topic_id="deadbeef", authorization="envelope")
    issues = _issues("ops/requests/b2bcbe11-deploy.dev.yaml", yaml.safe_dump(doc))
    assert len(issues) >= 3


def test_non_mapping_and_broken_yaml_fail_cleanly():
    assert _issues(_path(), "- 就是个列表")
    with pytest.raises(ManifestError):
        validate_manifest(_path(), "args: [unclosed")


def test_render_refuses_to_launder_a_bad_manifest():
    """render 不能把一份非法清单洗成格式正确的清单。"""
    with pytest.raises(ManifestError):
        render_manifest(_path(), _text(args={"commit_sha": "main"}))


# ---------------------------------------------------------------------------
# 卡面
# ---------------------------------------------------------------------------


def test_card_face_matches_the_response_schema():
    """services 会把 card_face() 直接喂给 OperationCardFace（extra=forbid），
    两边字段一旦对不上就是运行时炸，所以这里当契约钉住。"""
    path, text = _rendered()
    face = validate_manifest(path, text).card_face()

    card = OperationCardFace.model_validate(face)
    assert card.operation_id == "deploy.dev"
    assert card.blast_radius == "dev"
    assert card.args == {"commit_sha": SHA}
    assert card.reason and card.what and card.where and card.worst_case
    assert card.human_approvals_required == 1


# ---------------------------------------------------------------------------
# PR 文件范围守卫
# ---------------------------------------------------------------------------


def test_a_pr_with_no_manifest_is_none_of_the_guard_s_business():
    assert not is_ops_pr(["backend/app/main.py", "docs/x.md"])
    assert guard_issues(["backend/app/main.py", "docs/x.md"]) == []


def test_ops_directory_furniture_is_not_a_request():
    """ops/README.md、.gitkeep 是目录家具，不是请求 —— 引入这套东西的 PR
    本身就只动它们，不能被自己的规矩拦下。"""
    assert not is_ops_pr(["ops/README.md", "ops/requests/.gitkeep"])
    assert guard_issues(["ops/README.md", "ops/requests/.gitkeep"]) == []


def test_an_ops_pr_may_only_carry_manifests():
    issues = guard_issues(
        ["ops/requests/b2bcbe11-deploy.dev.yaml", "backend/app/domain/ops/registry.py"]
    )
    assert len(issues) == 1
    assert "registry.py" in issues[0]


def test_ops_pr_with_only_manifests_passes():
    assert (
        guard_issues(
            [
                "ops/requests/b2bcbe11-deploy.dev.yaml",
                "ops/requests/abcd1234-deploy.prod.yaml",
            ]
        )
        == []
    )


def test_guard_rejects_a_manifest_hidden_in_a_subdirectory():
    assert is_manifest_path("ops/requests/andy/x.yaml")
    issues = guard_issues(["ops/requests/andy/x.yaml"])
    assert len(issues) == 1
    assert "子目录" in issues[0]


# ---------------------------------------------------------------------------
# service：把分支上的清单读成卡面
# ---------------------------------------------------------------------------


def _fake_branch(monkeypatch, files: dict[str, str]) -> None:
    """假装 topic 分支上就这些文件。签名照抄 workspace.service 的真实签名 ——
    那两个函数的形状变了，这里必须跟着变，否则测试会替一份坏代码背书。"""

    def list_files(project_id: uuid.UUID, topic_id: uuid.UUID | None = None):
        return [{"path": p, "bytes": len(t.encode())} for p, t in files.items()]

    def read_file(
        project_id: uuid.UUID, path: str, topic_id: uuid.UUID | None = None
    ) -> str:
        return files[path]

    monkeypatch.setattr(ops_services.ws, "list_files", list_files)
    monkeypatch.setattr(ops_services.ws, "read_file", read_file)


def test_service_reads_manifests_and_ignores_everything_else(monkeypatch):
    _, text = _rendered()
    _fake_branch(
        monkeypatch,
        {
            "ops/requests/b2bcbe11-deploy.dev.yaml": text,
            "ops/README.md": "# 不是请求",
            "ops/requests/.gitkeep": "",
            "backend/app/main.py": "print()",
        },
    )

    requests = ops_services.OperationRequestService(
        uuid.uuid4(), topic_id=uuid.uuid4()
    ).list_requests()

    assert len(requests) == 1
    assert requests[0].valid
    assert requests[0].face is not None
    assert requests[0].face.operation_id == "deploy.dev"
    assert requests[0].face.blast_radius == "dev"


def test_service_surfaces_a_broken_manifest_instead_of_hiding_it(monkeypatch):
    """没过校验的请求必须在卡面上显示成「这张没过校验」——
    否则它看起来跟「压根没有请求」一模一样。"""
    _fake_branch(monkeypatch, {"ops/requests/b2bcbe11-deploy.dev.yaml": _text()})

    requests = ops_services.OperationRequestService(uuid.uuid4()).list_requests()

    assert len(requests) == 1
    assert not requests[0].valid
    assert requests[0].face is None
    assert requests[0].issues


def test_service_returns_manifests_in_a_stable_order(monkeypatch):
    _, dev = _rendered()
    _, backup = _rendered(
        operation_id="backup.restore_test",
        topic_id="abcd1234",
        args={"commit_sha": SHA},
    )
    _fake_branch(
        monkeypatch,
        {
            "ops/requests/b2bcbe11-deploy.dev.yaml": dev,
            "ops/requests/abcd1234-backup.restore_test.yaml": backup,
        },
    )

    paths = [
        r.path
        for r in ops_services.OperationRequestService(uuid.uuid4()).list_requests()
    ]
    assert paths == sorted(paths)


# ---------------------------------------------------------------------------
# CLI（CI 的必需检查跑的就是它）
# ---------------------------------------------------------------------------


def _cli():
    path = Path(__file__).resolve().parents[2] / "scripts" / "ops_manifest.py"
    spec = importlib.util.spec_from_file_location("ops_manifest_cli", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_check_agrees_from_backend_and_from_the_repo_root(tmp_path: Path):
    """`check ../ops/requests/x.yaml`（在 backend/ 下跑）和
    `check ops/requests/x.yaml`（在仓库根跑）必须给出同一个判断。"""
    cli = _cli()
    requests = tmp_path / "ops" / "requests"
    requests.mkdir(parents=True)
    _, text = _rendered()
    manifest = requests / "b2bcbe11-deploy.dev.yaml"
    manifest.write_text(text, encoding="utf-8")

    assert cli.logical_path(str(manifest)) == "ops/requests/b2bcbe11-deploy.dev.yaml"
    assert cli.main(["check", str(manifest)]) == 0


def test_cli_check_fails_on_a_tampered_manifest(tmp_path: Path):
    cli = _cli()
    requests = tmp_path / "ops" / "requests"
    requests.mkdir(parents=True)
    _, text = _rendered()
    doc = yaml.safe_load(text)
    doc["resolved"]["blast_radius"] = "none"
    manifest = requests / "b2bcbe11-deploy.dev.yaml"
    manifest.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    assert cli.main(["check", str(manifest)]) == 1


def test_cli_render_writes_the_resolved_block_back(tmp_path: Path):
    cli = _cli()
    requests = tmp_path / "ops" / "requests"
    requests.mkdir(parents=True)
    manifest = requests / "b2bcbe11-deploy.dev.yaml"
    manifest.write_text(_text(), encoding="utf-8")

    assert cli.main(["render", str(manifest)]) == 0
    assert cli.main(["check", str(manifest)]) == 0
    assert "registry 生成" in manifest.read_text(encoding="utf-8")


def test_cli_guard_exit_codes():
    cli = _cli()
    assert cli.main(["guard", "backend/app/main.py"]) == 0
    assert (
        cli.main(
            ["guard", "ops/requests/b2bcbe11-deploy.dev.yaml", "backend/app/main.py"]
        )
        == 1
    )


def test_cli_describe_prints_the_registry(capsys):
    cli = _cli()
    assert cli.main(["describe"]) == 0
    assert "deploy.prod" in capsys.readouterr().out
