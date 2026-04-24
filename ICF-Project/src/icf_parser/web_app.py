from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from src.icf_parser.service import generate_amendment_history_safe


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_PATH = PROJECT_ROOT / "TG-ICF模板" / "标准模板.docx"
TEMPLATES = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))
ALLOWED_DOWNLOADS: set[Path] = set()
ACCEPTANCE_ROOTS = (
    PROJECT_ROOT / "训练集" / "训练文件验收文件",
    PROJECT_ROOT / "测试集" / "验收文件",
)

app = FastAPI(title="ICF Revision Generator")


def _page_context(
    request: Request,
    *,
    template_path: str | None = None,
    acceptance_path: str = "",
    mode: str = "release",
    output_path: str = "",
    log_lines: list[str] | None = None,
    status: str = "idle",
    error_message: str = "",
    source_name: str = "",
    row_count: int = 0,
    preview_rows: list[dict[str, str]] | None = None,
    integrity_verdict: str = "待校验",
    report_path: str = "",
    delivery_status: str = "未执行",
    delivery_passed: bool = False,
    acceptance_diff_path: str = "",
    progress_path: str = "",
    blocking_issue_count: int = 0,
) -> dict[str, Any]:
    result_path = Path(output_path) if output_path else None
    return {
        "request": request,
        "template_path": template_path or str(DEFAULT_TEMPLATE_PATH),
        "acceptance_path": acceptance_path,
        "mode": mode,
        "output_path": output_path,
        "log_lines": log_lines or [],
        "status": status,
        "error_message": error_message,
        "source_name": source_name,
        "row_count": row_count,
        "preview_rows": preview_rows or [],
        "result_exists": bool(result_path and result_path.exists()),
        "integrity_verdict": integrity_verdict,
        "report_path": report_path,
        "delivery_status": delivery_status,
        "delivery_passed": delivery_passed,
        "acceptance_diff_path": acceptance_diff_path,
        "progress_path": progress_path,
        "blocking_issue_count": blocking_issue_count,
    }


def _preview_rows(rows: list[Any], limit: int = 5) -> list[dict[str, str]]:
    preview: list[dict[str, str]] = []
    for row in rows[:limit]:
        preview.append(
            {
                "topic": row.topic,
                "section_page": row.section_page,
                "reason": row.reason,
            }
        )
    return preview


def _guess_acceptance_path(source_name: str) -> Path | None:
    stem = Path(source_name).stem
    candidates = (
        f"{stem}-验收.docx",
        f"{stem}验收标准文档.docx",
        f"{stem}-验收标准文档.docx",
    )
    for root in ACCEPTANCE_ROOTS:
        for candidate in candidates:
            path = root / candidate
            if path.exists():
                return path
    return None


@app.get("/download")
def download(path: str) -> FileResponse:
    file_path = Path(path).resolve()
    if file_path.suffix.lower() != ".docx":
        raise HTTPException(status_code=400, detail="invalid download target")
    if file_path not in ALLOWED_DOWNLOADS:
        raise HTTPException(status_code=400, detail="download target not registered")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request, "index.html", _page_context(request))


@app.post("/generate", response_class=HTMLResponse)
async def generate(
    request: Request,
    source_file: UploadFile = File(...),
    template_path: str = Form(...),
    output_path: str = Form(...),
    acceptance_path: str = Form(""),
    mode: str = Form("release"),
) -> HTMLResponse:
    temp_suffix = Path(source_file.filename or "source.docx").suffix or ".docx"
    with NamedTemporaryFile(delete=False, suffix=temp_suffix) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(await source_file.read())

    destination = Path(output_path)
    resolved_acceptance_path = Path(acceptance_path).resolve() if acceptance_path.strip() else _guess_acceptance_path(source_file.filename or "")

    try:
        result = generate_amendment_history_safe(
            source_docx=temp_path,
            template_docx=Path(template_path),
            output_docx=destination,
            acceptance_docx=resolved_acceptance_path,
            progress_dir=PROJECT_ROOT / "output" / "progress" if resolved_acceptance_path is not None else None,
            mode=mode,
        )
        result_path = result.output_path.resolve()
        ALLOWED_DOWNLOADS.add(result_path)
        log_lines = [
            f"输入文件：{source_file.filename}",
            f"模板文件：{template_path}",
            f"验收文件：{resolved_acceptance_path or '未提供'}",
            f"运行模式：{mode}",
            f"输出文件：{result_path}",
            f"结构校验：{'通过' if result.integrity.is_valid else '失败'}",
            f"交付状态：{result.delivery_status}",
            f"交付判定：{'通过' if result.delivery_passed else '未通过'}",
            f"报告文件：{result.report_path}",
            f"交付差异报告：{result.acceptance_diff_path or '未提供'}",
            f"复盘文件：{result.progress_path or '未提供'}",
            f"阻断项数量：{len(result.blocking_issues)}",
            f"已写入 {result.row_count} 条修订记录",
            "执行完成",
        ]
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            _page_context(
                request,
                template_path=template_path,
                acceptance_path=str(resolved_acceptance_path) if resolved_acceptance_path is not None else "",
                mode=mode,
                output_path=str(result_path),
                log_lines=log_lines,
                status="success",
                source_name=source_file.filename or "",
                row_count=result.row_count,
                preview_rows=_preview_rows(result.rows),
                integrity_verdict="通过" if result.integrity.is_valid else "失败",
                report_path=str(result.report_path),
                delivery_status=result.delivery_status,
                delivery_passed=result.delivery_passed,
                acceptance_diff_path=str(result.acceptance_diff_path) if result.acceptance_diff_path else "",
                progress_path=str(result.progress_path) if result.progress_path else "",
                blocking_issue_count=len(result.blocking_issues),
            ),
        )
    except Exception as exc:
        log_lines = [
            f"输入文件：{source_file.filename}",
            f"模板文件：{template_path}",
            f"验收文件：{resolved_acceptance_path or '未提供'}",
            f"运行模式：{mode}",
            f"输出目标：{destination}",
            f"错误详情：{exc}",
            "执行失败",
        ]
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            _page_context(
                request,
                template_path=template_path,
                acceptance_path=str(resolved_acceptance_path) if resolved_acceptance_path is not None else acceptance_path,
                mode=mode,
                output_path=str(destination),
                log_lines=log_lines,
                status="error",
                error_message=str(exc),
                source_name=source_file.filename or "",
            ),
        )
    finally:
        temp_path.unlink(missing_ok=True)
