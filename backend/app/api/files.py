import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import AppSettings, CurrentUser, DatabaseSession
from app.db.entities import Document
from app.schemas.files import FileUploadResponse
from app.services.documents import index_uploaded_document

router = APIRouter(prefix="/api/v1/files", tags=["files"])


@router.post("", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_file(
    request: Request,
    current_user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    file: UploadFile = File(...),
) -> FileUploadResponse:
    storage_dir = Path(settings.document_storage_path)
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to create storage directory",
        ) from exc

    uploaded_name = file.filename or "upload"
    ext = Path(uploaded_name).suffix
    unique_name = f"{uuid.uuid4().hex}{ext}"
    dest_path = storage_dir / unique_name

    sha256 = hashlib.sha256()
    try:
        with dest_path.open("wb") as out_file:
            while chunk := file.file.read(8192):
                out_file.write(chunk)
                sha256.update(chunk)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write uploaded file",
        ) from exc
    finally:
        file.file.close()

    checksum = sha256.hexdigest()

    doc = Document(
        user_id=current_user.id,
        filename=uploaded_name,
        storage_path=str(dest_path),
        mime_type=file.content_type,
        checksum=checksum,
        source="upload",
    )
    db.add(doc)
    try:
        db.commit()
        db.refresh(doc)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc

    index_uploaded_document(db, current_user.id, doc, settings)
    # Ein Foto oder Scan wartet jetzt auf das Abschreiben: den Indexer wecken,
    # statt bis zu seinem naechsten Durchlauf zu warten.
    indexer = getattr(request.app.state, "material_indexer", None)
    if indexer is not None and doc.status == "queued":
        indexer.wake()

    return FileUploadResponse(
        id=doc.id,
        filename=doc.filename,
        mime_type=doc.mime_type,
        path=doc.storage_path,
        checksum=doc.checksum,
        status=doc.status,
        created_at=doc.created_at,
    )
