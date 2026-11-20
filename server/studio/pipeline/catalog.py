"""Find real robots for a design version, and compare one candidate with it. Pure functions: no project or store access."""
import asyncio
from collections import Counter
from dataclasses import dataclass

from .. import families
from ..agents import analysis as analysis_agent
from ..catalog import compare, search
from ..catalog.extract import extract
from ..catalog.fetch import safe_download
from ..catalog.model import candidate_concept, qualify_checks
from ..ingest.docs import extract_text
from ..llm.usage import set_context
from ..schemas import Analysis
from ..sim import report
from ..sim.task import simulate
from ..util import now, slug
from .fleet import member_view


class DatasheetError(ValueError):
    """The datasheet could not be fetched or read; the user can upload it instead."""
    code = 'datasheet_download'

    def __init__(self, reason):
        super().__init__(f'Datasheet download failed: {reason}. Upload the datasheet (PDF or text) instead.')


@dataclass
class Document:
    text: str
    source: str          # URL the text came from, or a note such as "uploaded document x.pdf"
    title: str = ''      # file name of a downloaded document
    downloaded: bool = False


async def find(llms, version, progress, download=safe_download, limit=6, member=None):
    """Search the web for robots that fit `version` (normally the approved one); on a multi-robot version, for
    robot `member` (None = the primary robot, which the top-level fields already describe). Returns
    {'candidates': [Candidate], 'dropped': {reason: count}}; nothing is stored here."""
    version = member_view(version, member)
    progress('Searching the web for robots that fit')
    set_context(task='search')
    dropped = Counter()
    candidates = await search.search(llms.for_role('search'), version.spec, version.requirements, version.robot,
                                     limit=limit, dropped=dropped)
    progress(f'Found {len(candidates)} candidates; screening payload and reach')
    if dropped:
        reasons = ', '.join(f'{reason}' + (f' ({n})' if n > 1 else '') for reason, n in dropped.items())
        progress(f'{sum(dropped.values())} price{"s" if sum(dropped.values()) != 1 else ""} dropped: {reasons}')
    if any(not c.image_url for c in candidates):
        progress('Looking up product images')
        await search.fill_images(candidates, download)
    return {'candidates': candidates, 'dropped': dict(dropped)}


async def fetch_document(download, url):
    got = await download(url)
    return extract_text(got.bytes, got.filename), got.final_url or url, got.filename


async def datasheet_text(candidate, datasheet_url, progress, download, stored=None, stored_source=''):
    """A Document. An explicit URL is downloaded; otherwise a stored (uploaded) document text is used, else the
    candidate's datasheet link, else (or when that link fails) the candidate's product page."""
    url = (datasheet_url or '').strip()
    if stored and not url:
        return Document(stored, stored_source or 'uploaded document')
    attempts = [(url, 'the datasheet')] if url else []
    if not url and candidate.datasheet_url:
        attempts.append((candidate.datasheet_url, 'the datasheet'))
    if not url and candidate.product_url:
        attempts.append((candidate.product_url, 'the product page'))
    if not attempts:
        raise DatasheetError('this candidate has no datasheet link')
    last = None
    for target, what in attempts:
        progress('Downloading the datasheet' if what == 'the datasheet'
                 else 'No datasheet available; using the product page as the document')
        try:
            text, source, title = await fetch_document(download, target)
        except Exception as error:  # UnsafeURL, HTTP and network errors, wrong type, unreadable file
            last = error
            continue
        return Document(text, source, title, downloaded=True)
    raise DatasheetError(str(last) or last.__class__.__name__) from last


def unsupported_note(candidate):
    return (f"{candidate.model} is listed as family '{candidate.family}'; Studio can model serial arms, gantries, "
            'mobile bases and mobile manipulators. Its listed specs and the quick screen still apply.')


async def analyse(llms, version, candidate, datasheet_url, progress, download=safe_download, stored=None,
                  stored_source='', member=None):
    """Compare `candidate` with `version` (the approved design). `stored` is a previously saved or uploaded document
    text. Returns {'analysis': Analysis, 'document': {'text', 'url', 'title'} | None}; `document` is set only when
    this call downloaded one, so the caller can keep it. Nothing is stored here. `member` picks a robot of a
    multi-robot version (None = the primary one)."""
    version = member_view(version, member)
    if candidate.family not in families.REGISTRY:
        raise ValueError(unsupported_note(candidate))
    doc = await datasheet_text(candidate, datasheet_url, progress, download, stored, stored_source)
    progress('Reading the datasheet and checking every quote')
    set_context(task='extract')
    mspec = await extract(llms.for_role('extract'), candidate, doc.text, doc.source)
    concept, unverified, provenance = candidate_concept(mspec, version.robot, version.spec, candidate)
    concept = concept.model_copy(update={'base': version.robot.base})
    progress('Running the same task with the candidate')
    family = families.get(concept.family)
    urdf = family.build_urdf(family.parse(concept.params), slug(concept.name))
    sim = await asyncio.to_thread(simulate, version.spec, version.requirements, version.scene, concept)
    checks = qualify_checks(sim.checks, provenance)
    sim = sim.model_copy(update={'checks': checks, 'ok': report.all_ok(checks)})
    differences = compare.differences(version, sim, concept, provenance)
    progress('Explaining the differences')
    set_context(task='analysis')
    differences = await analysis_agent.run(llms.for_role('analysis'), differences, version.spec)
    analysis = Analysis(candidate_id=candidate.id, manufacturer_spec=mspec, robot=concept, urdf=urdf, sim=sim,
                        differences=differences, unverified=unverified, provenance=provenance, created=now())
    document = {'text': doc.text, 'url': doc.source, 'title': doc.title} if doc.downloaded else None
    return {'analysis': analysis, 'document': document}
