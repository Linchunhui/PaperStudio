from typing import Literal
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from . import bibliography, portability

router=APIRouter(prefix='/api',tags=['portability'])


@router.post('/bibliography/preview')
def preview(file: UploadFile=File(...)):
    try: content=file.file.read(bibliography.MAX_BYTES+1)
    finally: file.file.close()
    return bibliography.parse(content,file.filename or '')


class ImportInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    entries:list[dict]=Field(min_length=1,max_length=bibliography.MAX_ENTRIES)


@router.get('/projects/{project_id}/bibliography')
def list_references(project_id:str):
    return {'entries':bibliography.entries(project_id)}


@router.post('/projects/{project_id}/bibliography',status_code=201)
def save_references(project_id:str,body:ImportInput):
    return bibliography.save(project_id,body.entries)


@router.get('/projects/{project_id}/export')
def export(project_id:str,format:Literal['zip','html','md','bib','json']='zip',
           include_children:bool=False,include_shared:bool=False,include_generated:bool=False):
    import json
    data=portability.snapshot(project_id,include_children=include_children,
        include_shared=include_shared,include_generated=include_generated)
    contents={'zip':lambda:portability.archive(data),'html':lambda:portability.html_document(data),
        'md':lambda:portability.markdown(data),'json':lambda:json.dumps(data,ensure_ascii=False,indent=2),
        'bib':lambda:bibliography.bibtex([p['metadata'] for p in data['papers']]+data['references'])}
    media={'zip':'application/zip','html':'text/html','md':'text/markdown','json':'application/json','bib':'application/x-bibtex'}
    return Response(contents[format](),media_type=media[format],headers={
        'Content-Disposition':f'attachment; filename="paperstudio-project.{format}"',
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
