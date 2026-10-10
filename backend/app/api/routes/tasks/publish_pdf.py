"""从 PDF 批量拟稿与确认发布。"""

from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.routes.tasks._common import (
    ConfirmTaskPublishFromPdfRequest,
    _attachment_service,
    _create_task_entity,
    _task_attachment_service,
    get_task_pdf_draft_service,
)

# 给了题目的「给 AI 队友的指导」(#944)：请求体沿用 项目集 PATCH 那个严格模型，
# 读写与引用校验在 app.api.task_teaching 里，接口形状在 app.api.task_serialization。
from app.api.task_serialization import (
    _enrich_task_models,
    _task_to_api_model,
    _uploaded_attachment_to_api,
)
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.auth.space_access import may_publish_in_space
from app.core.errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
)
from app.db.session import get_db
from app.domain.space.repositories import SpaceRepository
from app.domain.tag.repositories import TagRepository
from app.domain.task.inputs import (
    apply_pdf_task_options,
    pdf_attachment_ids,
)
from app.domain.task.models import (
    Task,
)
from app.domain.task.task_pdf_draft_service import TaskPdfDraftService

router = APIRouter(prefix="/tasks")

#: 拟稿用的 PDF 多大为止。比附件那一档（10MB）宽，因为一份卷子本来就比一张截图大。
MAX_PDF_BYTES = 15 * 1024 * 1024


@router.post(
    "/publish/from-pdf/preview",
    summary="Preview Task Drafts From PDF",
)
async def preview_task_from_pdf(
    space_id: Annotated[int, Form(alias="spaceId")],
    pdf_file: Annotated[UploadFile, File(alias="file")],
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    draft_service: TaskPdfDraftService = Depends(get_task_pdf_draft_service),
    category_id: Annotated[int | None, Form(alias="categoryId")] = None,
    template_index: Annotated[int, Form(alias="templateIndex")] = 0,
    forced_submitter_type: Annotated[str | None, Form(alias="submitterType")] = None,
    max_tasks: Annotated[int, Form(alias="maxTasks")] = 5,
) -> dict:
    filename = (pdf_file.filename or "").lower()
    content_type = (pdf_file.content_type or "").lower()
    if not filename.endswith(".pdf") and "pdf" not in content_type:
        raise BadRequestError("Only PDF file is supported")
    if max_tasks < 1 or max_tasks > 20:
        raise BadRequestError("maxTasks must be between 1 and 20")

    space = await SpaceRepository(session=db).get_by_id(space_id)
    if space is None:
        raise NotFoundError("Space not found")

    # 发题的门，与 ``_create_task_entity`` 是同一句、同一处口径
    # （``may_publish_in_space``：「这个板里的人都能发」）。预览是发题的前半截 ——
    # `confirm` 那条路逐条落进 `_create_task_entity` 时已经过这道门，只有预览这一
    # 条漏着：从前只 `require_auth_user`，于是板外的登录用户拿别人的 `spaceId`
    # （小整数、可枚举）就能让模型为这块板花掉 token，并把 `task_templates` 原样
    # 读回去（返回体的 `templateUsed`）—— 而同一份模板在 `GET /spaces/{spaceId}`
    # 上要先 ``_ensure_space_visible`` 才看得到。
    #
    # 门放在读 PDF 之前：挡的是「谁可以让这块板干活」，不是「响应里少写几个字段」。
    # 措辞照抄发题那道门（它自己那句「board manager」与判据的注释在
    # `space_access.may_publish_in_space` 里已有交代）——两处一句话，不另立说法。
    if not await may_publish_in_space(
        session=db, space_id=space_id, user_id=auth_user.user_id
    ):
        raise ForbiddenError("Only a board manager can publish tasks here")

    # 限读：多读一个字节就够判「超了」，读回来的长度也只到这里。超限由下面那句判，
    # 不是由内存分配来判——从前的顺序是先整份读完再判，判的是已经花掉的内存。
    pdf_bytes = await pdf_file.read(MAX_PDF_BYTES + 1)
    if not pdf_bytes:
        raise BadRequestError("Uploaded PDF is empty")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise BadRequestError("PDF file is too large (max 15MB)")

    # No category picked: the space's default one, by id — its name is the
    # owner's to change and depends on the creator's language.
    resolved_category_id = (
        category_id if category_id is not None else space.default_category_id
    )

    default_topic_ids: list[int] = []
    global_topic_repo = TagRepository(session=db)
    default_topic = await global_topic_repo.get_by_name("计算机系统")
    if default_topic is not None:
        default_topic_ids.append(default_topic.id)

    template = draft_service.pick_template(space.task_templates or [], template_index)
    async with draft_service.charged_to(db, auth_user.user_id):
        (
            drafts,
            token_used,
            illustrations,
        ) = await draft_service.generate_task_payloads_from_pdf(
            pdf_bytes=pdf_bytes,
            template=template,
            space_id=space_id,
            category_id=resolved_category_id,
            forced_submitter_type=forced_submitter_type,
            user_id=auth_user.user_id,
            default_topic_ids=default_topic_ids,
            max_tasks=max_tasks,
        )

    # 解析出来的东西落成**发布者本人名下**的附件行，把 id 交回给前端去勾：原 PDF 与
    # 那几张插图在服务端手上，只有这里能登记它们。挂在 ``meta.uploaderId`` 上的名字
    # 必须是调用者 —— 确认发布那一步拿 ``TaskAttachmentService`` 挂文件，它有一道校
    # 验是「只能挂自己上传的文件」（附件 id 是可猜的连续整数，不校验就等于把别人的
    # 文件挂到自己的题上）。服务端自己建的行如果不署名，这一批文件会被自己挡住。
    #
    # 提前落库的代价是「解析了却没发布」时留下没人引用的文件，这与素材库、PDF 导入
    # 抽图今天的处境一样（``TaskAttachmentService.attach_uploaded`` 里那段说明）。
    attachments = _attachment_service(db)
    pdf_attachment = await attachments.upload(
        file=BytesIO(pdf_bytes),
        filename=pdf_file.filename or "document.pdf",
        content_type="application/pdf",
        uploader_id=auth_user.user_id,
    )
    illustration_attachments = [
        await attachments.register_stored(
            filename=illustration.filename,
            content_type=illustration.content_type,
            storage_key=illustration.storage_key,
            url=illustration.url,
            size=illustration.size,
            uploader_id=auth_user.user_id,
            file_hash=illustration.file_hash,
        )
        for illustration in illustrations
    ]

    return {
        "code": 200,
        "message": "Task drafts previewed from PDF successfully.",
        "data": {
            "drafts": drafts,
            "templateUsed": template,
            "tokenUsed": token_used,
            # 可以勾的东西：原 PDF 一份，抽出的插图若干张。前端照这个画勾选框，
            # 勾中的 id 由确认发布那条请求带回来。
            "attachments": {
                "pdf": _uploaded_attachment_to_api(pdf_attachment),
                "images": [
                    _uploaded_attachment_to_api(attachment)
                    for attachment in illustration_attachments
                ],
            },
        },
    }


@router.post(
    "/publish/from-pdf/confirm",
    summary="Confirm Publish Task Drafts From PDF",
)
async def confirm_publish_task_from_pdf(
    payload: ConfirmTaskPublishFromPdfRequest,
    db=Depends(get_db),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    drafts = payload.drafts
    task_options = payload.task_options
    if not drafts:
        raise BadRequestError("drafts is required")
    if len(drafts) > 20:
        raise BadRequestError("At most 20 drafts can be published at once")

    # 勾中的附件跟着**每一道**生成出来的题走（不是随机分给某一道）：预览那一步把原
    # PDF 与抽出的插图报了回来，人在这里勾「原 PDF / 抽出的插图」，所以这一批题拿到
    # 的是同一份材料。不勾就一个都不带 —— 默认行为与这条路今天的样子完全一样。
    attachment_ids = pdf_attachment_ids(task_options)
    if attachment_ids:
        # **先校验、后建题**：一个挂不上的 id（不存在的、别人的、已经在别的题上的）
        # 让整条请求立刻失败，一道题都不建。异常时 ``get_db`` 会回滚整个请求，所以顺
        # 序本身不改变结果；但「先把题造出来、再靠回滚收走」把一批没材料的题压在一个
        # 请求级保证上，而这件事本来可以不做。
        await _task_attachment_service(db).ensure_attachable(
            user_id=auth_user.user_id, attachment_ids=attachment_ids
        )

    created_tasks: list[Task] = []
    space_id: int | None = None
    for draft in drafts:
        task_payload = apply_pdf_task_options(draft=draft, task_options=task_options)
        created = await _create_task_entity(
            payload=task_payload,
            db=db,
            creator_user_id=auth_user.user_id,
        )
        created_tasks.append(created)
        if space_id is None and "space" in task_payload:
            try:
                space_id = int(task_payload["space"])
            except (TypeError, ValueError):
                pass

    if attachment_ids:
        # 一道题一次挂完再进下一道？不 —— 校验（存在 / 是我的 / 还没挂在别处）在这一
        # 批**开始之前**判一次，理由见 ``attach_uploaded_to_tasks``。校验不过就整个请
        # 求回滚（``get_db`` 在异常时 rollback），不会留下一半带材料、一半不带的题。
        await _task_attachment_service(db).attach_uploaded_to_tasks(
            tasks=created_tasks,
            user_id=auth_user.user_id,
            attachment_ids=attachment_ids,
        )

    # Commit before answering. ``get_db`` commits in its teardown, which FastAPI
    # runs after the response has gone out, so a client told the drafts are
    # published could open the review queue on its next request and not find them.
    # Same reason as the commit in ``create_task``.
    await db.commit()

    task_models = [_task_to_api_model(task) for task in created_tasks]
    if space_id is not None:
        task_models = await _enrich_task_models(db, task_models, space_id=space_id)

    return {
        "code": 200,
        "message": "Task drafts published successfully.",
        "data": {
            "tasks": task_models,
            "count": len(created_tasks),
        },
    }
