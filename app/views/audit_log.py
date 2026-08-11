from flask import Blueprint, render_template, request

from ..models import AuditEntry
from ..security import login_required

bp = Blueprint("audit_log", __name__)


@bp.route("/audit")
@login_required
def index():
    page = max(1, int(request.args.get("page") or 1))
    query = AuditEntry.query.order_by(AuditEntry.ts.desc())
    entries = query.limit(100).offset((page - 1) * 100).all()
    return render_template("audit.html", entries=entries, page=page)
