"""Find real robots for the approved design, and compare one candidate with the generated design."""
import asyncio

from .. import families
from ..agents import analysis as analysis_agent
from ..catalog import compare, search
from ..catalog.extract import extract
from ..catalog.fetch import safe_download
from ..catalog.model import candidate_concept
from ..ingest.docs import extract_text
from ..llm.usage import set_context
from ..schemas import Analysis
from ..sim.task import simulate
from ..util import now, slug


class DatasheetError(ValueError):
    """The datasheet could not be fetched or read; the user can upload it instead."""
    code = 'datasheet_download'

    def __init__(self, reason):
        super().__init__(f'Datasheet download failed: {reason}. Upload the datasheet (PDF or text) instead.')


def approved_version(project):
    version = project.approved()
    if version is None:
        raise ValueError('Approve a design version first')
    return version


async def find(llms, project, progress):
    version = approved_version(project)
    progress('Searching the web for robots that fit')
    set_context(task='search')
    candidates = await search.search(llms.for_role('search'), version.spec, version.requirements, version.robot)
    progress(f'Found {len(candidates)} candidates; screening payload and reach')
    project.save_candidates(candidates)
    return {'candidates': [c.model_dump(mode='json') for c in candidates]}


async def datasheet_text(project, candidate, datasheet_url, progress, download):
    """(text, source). An explicit URL is downloaded; otherwise an uploaded document is used, else the candidate's link."""
    stored = project.document(candidate.id)
    url = (datasheet_url or '').strip()
    if stored and not url:
        return stored, project.document_source(candidate.id) or 'uploaded document'
    url = url or candidate.datasheet_url
    if not url:
        raise DatasheetError('this candidate has no datasheet link')
    progress('Downloading the datasheet')
    try:
        got = await download(url)
        text = extract_text(got.bytes, got.filename)
    except Exception as error:  # UnsafeURL, HTTP and network errors, wrong type, unreadable file
        raise DatasheetError(str(error) or error.__class__.__name__) from error
    project.save_document(candidate.id, text, got.final_url or url)
    return text, got.final_url or url


async def analyse(llms, project, cid, datasheet_url, progress, download=safe_download):
    version = approved_version(project)
    candidate = project.candidate(cid)
    text, source = await datasheet_text(project, candidate, datasheet_url, progress, download)
    progress('Reading the datasheet and checking every quote')
    set_context(task='extract')
    mspec = await extract(llms.for_role('extract'), candidate, text, source)
    concept, unverified = candidate_concept(mspec, version.robot, version.spec, candidate)
    concept = concept.model_copy(update={'base': version.robot.base})
    progress('Running the same task with the candidate')
    family = families.get(concept.family)
    urdf = family.build_urdf(family.parse(concept.params), slug(concept.name))
    sim = await asyncio.to_thread(simulate, version.spec, version.requirements, version.scene, concept)
    differences = compare.differences(version, sim, concept)
    progress('Explaining the differences')
    set_context(task='analysis')
    differences = await analysis_agent.run(llms.for_role('analysis'), differences, version.spec)
    analysis = Analysis(candidate_id=candidate.id, manufacturer_spec=mspec, robot=concept, urdf=urdf, sim=sim,
                        differences=differences, unverified=unverified, created=now())
    project.save_analysis(analysis)
    return analysis
