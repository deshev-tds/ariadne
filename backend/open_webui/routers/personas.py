import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_session
from open_webui.models.models import Models
from open_webui.models.personas import PersonaForm, PersonaListResponse, PersonaModel, Personas
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()


def _ensure_workspace_access(request: Request, user, db: Session) -> None:
    if user.role != "admin" and not has_permission(
        user.id, "workspace.models", request.app.state.config.USER_PERMISSIONS, db=db
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )


def _validate_bound_model(bound_model_id: Optional[str], user, db: Session) -> None:
    if not bound_model_id:
        return

    model = Models.get_model_by_id(bound_model_id, db=db)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bound model must be an enabled Open WebUI model or preset.",
        )

    if user.role != "admin" and model.user_id != user.id:
        # Access-granted models are still valid; the chat completion path will
        # enforce access again at runtime, so we allow referenced models here.
        return


@router.get("/", response_model=list[PersonaModel])
@router.get("/list", response_model=list[PersonaModel])
async def get_personas(
    request: Request,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_workspace_access(request, user, db)
    return Personas.get_personas_by_user_id(user.id, db=db)


@router.get("/id/{id}", response_model=Optional[PersonaModel])
async def get_persona_by_id(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_workspace_access(request, user, db)
    persona = Personas.get_persona_by_id_and_user_id(id, user.id, db=db)
    if persona is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    return persona


@router.post("/create", response_model=Optional[PersonaModel])
async def create_persona(
    request: Request,
    form_data: PersonaForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_workspace_access(request, user, db)
    _validate_bound_model(form_data.bound_model_id, user, db)

    persona = Personas.insert_new_persona(user.id, form_data, db=db)
    if persona is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )
    return persona


@router.post("/update", response_model=Optional[PersonaModel])
async def update_persona(
    request: Request,
    form_data: PersonaForm,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_workspace_access(request, user, db)
    if not form_data.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Persona id is required.",
        )

    _validate_bound_model(form_data.bound_model_id, user, db)
    persona = Personas.update_persona_by_id(form_data.id, user.id, form_data, db=db)
    if persona is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    return persona


@router.post("/toggle", response_model=Optional[PersonaModel])
async def toggle_persona(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_workspace_access(request, user, db)
    persona = Personas.toggle_persona_by_id(id, user.id, db=db)
    if persona is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    return persona


@router.post("/duplicate", response_model=Optional[PersonaModel])
async def duplicate_persona(
    request: Request,
    id: str,
    user=Depends(get_verified_user),
    db: Session = Depends(get_session),
):
    _ensure_workspace_access(request, user, db)
    persona = Personas.duplicate_persona_by_id(id, user.id, db=db)
    if persona is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    return persona
