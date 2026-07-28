from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from athenaeum.api.deps import CurrentUserDep, SessionDep
from athenaeum.domain.models import PaperOut, ProjectCreate, ProjectOut, ProjectUpdate, SynthesisOut
from athenaeum.storage.models import Paper, Project, ProjectMember, ProjectPaper, Synthesis

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, session: SessionDep, user: CurrentUserDep
) -> Project:
    project = Project(
        title=payload.title,
        description=payload.description,
        license=payload.license,
        is_public=payload.is_public,
        owner_id=user.id,
    )
    session.add(project)
    await session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    await session.commit()
    await session.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
async def list_projects(session: SessionDep, user: CurrentUserDep) -> list[Project]:
    rows = await session.scalars(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id)
        .order_by(Project.created_at.desc())
    )
    return list(rows)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: UUID, session: SessionDep, user: CurrentUserDep
) -> Project:
    stmt = (
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(Project.id == project_id, ProjectMember.user_id == user.id)
    )
    project = await session.scalar(stmt)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.get("/{project_id}/papers", response_model=list[PaperOut])
async def list_project_papers(
    project_id: UUID, session: SessionDep, user: CurrentUserDep
) -> list[Paper]:
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a project member")
    rows = await session.scalars(
        select(Paper)
        .join(ProjectPaper, ProjectPaper.paper_id == Paper.id)
        .where(ProjectPaper.project_id == project_id)
        .order_by(Paper.created_at.desc())
    )
    return list(rows)


@router.get("/{project_id}/syntheses", response_model=list[SynthesisOut])
async def list_project_syntheses(
    project_id: UUID, session: SessionDep, user: CurrentUserDep
) -> list[Synthesis]:
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a project member")
    rows = await session.scalars(
        select(Synthesis)
        .where(Synthesis.project_id == project_id)
        .order_by(Synthesis.run_at.desc())
    )
    return list(rows)


@router.delete("/{project_id}/papers/{paper_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_paper_from_project(
    project_id: UUID,
    paper_id: UUID,
    session: SessionDep,
    user: CurrentUserDep,
) -> None:
    membership = await session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a project member")

    link = await session.scalar(
        select(ProjectPaper).where(
            ProjectPaper.project_id == project_id,
            ProjectPaper.paper_id == paper_id,
        )
    )
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paper not in project")

    await session.delete(link)
    await session.commit()


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    session: SessionDep,
    user: CurrentUserDep,
) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if project.owner_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the project owner can update it")

    if payload.title is not None:
        project.title = payload.title
    if payload.description is not None:
        project.description = payload.description
    if payload.is_public is not None:
        project.is_public = payload.is_public

    await session.commit()
    await session.refresh(project)
    return project
