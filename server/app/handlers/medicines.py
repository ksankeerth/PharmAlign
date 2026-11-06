"""Medicines and companies, the reads the case screens make. Licences and their writes come with the government record."""
from ..ops import op
from ..paging import paginate
from ..problems import not_found
from ..stores import STORES
from ..tables import ROW

MEDICINES = STORES["medicine"]
PARTIES = STORES["party"]
LICENCES = STORES["licence"]
RELIANCE = STORES["reliance_basis"]


def matches(search, *texts) -> bool:
    needle = (search or "").strip().lower()
    return not needle or any(needle in (t or "").lower() for t in texts)


@op("GET", "/api/v1/medicines")
async def list_medicines(req, ctx):
    found = [x for x in await MEDICINES.list(ctx, order_by=MEDICINES.row.id) if matches(req.query.search, x.name, *x.substances)]
    return paginate(found, req.query)


@op("GET", "/api/v1/medicines/{medicine_id}")
async def get_medicine(req, ctx):
    return await MEDICINES.require(ctx, req.path.medicine_id, "Medicine")


@op("GET", "/api/v1/parties")
async def list_parties(req, ctx):
    found = [p for p in await PARTIES.list(ctx, order_by=PARTIES.row.id) if matches(req.query.search, p.name)]
    return paginate(found, req.query)


@op("GET", "/api/v1/licences")
async def list_licences(req, ctx):
    row = ROW["licence"]
    where = [c for c in (row.medicine_id == req.query.medicine_id if req.query.medicine_id else None, row.gov_status == req.query.gov_status if req.query.gov_status else None) if c is not None]
    return await LICENCES.page(ctx, req.query, *where)


@op("GET", "/api/v1/licences/{licence_id}")
async def get_licence(req, ctx):
    return await LICENCES.require(ctx, req.path.licence_id, "Licence")


@op("GET", "/api/v1/licences/{licence_id}/reliance-basis")
async def licence_reliance(req, ctx):
    if await LICENCES.get(ctx, req.path.licence_id) is None:
        not_found("Licence", req.path.licence_id)
    row = ROW["reliance_basis"]
    return await RELIANCE.page(ctx, req.query, row.target_type == "licence", row.target_id == req.path.licence_id)
