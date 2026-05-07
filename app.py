import csv
import io
import sqlite3
from flask import Flask, g, redirect, render_template, request, send_file, url_for, jsonify

app = Flask(__name__)
DATABASE = "database.db"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS estimates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS contractors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS contractor_works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contractor_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            unit TEXT NOT NULL,
            default_price REAL DEFAULT 0,
            FOREIGN KEY (contractor_id) REFERENCES contractors(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS material_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            unit TEXT NOT NULL,
            default_price REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS estimate_contractors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            estimate_id INTEGER NOT NULL,
            contractor_id INTEGER NOT NULL,
            FOREIGN KEY (estimate_id) REFERENCES estimates(id) ON DELETE CASCADE,
            FOREIGN KEY (contractor_id) REFERENCES contractors(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            estimate_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            area REAL DEFAULT 0,
            FOREIGN KEY (estimate_id) REFERENCES estimates(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            contractor_id INTEGER,
            contractor_work_id INTEGER,
            name TEXT NOT NULL,
            unit TEXT NOT NULL,
            quantity REAL DEFAULT 0,
            price REAL DEFAULT 0,
            total REAL DEFAULT 0,
            is_electric INTEGER DEFAULT 0,
            FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE,
            FOREIGN KEY (contractor_id) REFERENCES contractors(id) ON DELETE SET NULL,
            FOREIGN KEY (contractor_work_id) REFERENCES contractor_works(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            catalog_material_id INTEGER,
            name TEXT NOT NULL,
            quantity REAL DEFAULT 0,
            price REAL DEFAULT 0,
            total REAL DEFAULT 0,
            FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE,
            FOREIGN KEY (catalog_material_id) REFERENCES material_catalog(id) ON DELETE SET NULL
        );
        """
    )
    db.commit()


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def recalc_totals(estimate_id):
    db = get_db()
    db.execute("UPDATE works SET total = quantity * price WHERE room_id IN (SELECT id FROM rooms WHERE estimate_id = ?)", (estimate_id,))
    db.execute("UPDATE materials SET total = quantity * price WHERE room_id IN (SELECT id FROM rooms WHERE estimate_id = ?)", (estimate_id,))
    db.commit()


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/estimates")
def estimates_page():
    items = get_db().execute("SELECT * FROM estimates ORDER BY id DESC").fetchall()
    return render_template("estimates.html", estimates=items)


@app.route("/estimates/create", methods=["POST"])
def create_estimate():
    name = request.form.get("name", "").strip()
    if name:
        get_db().execute("INSERT INTO estimates(name) VALUES(?)", (name,))
        get_db().commit()
    return redirect(url_for("estimates_page"))


@app.route("/estimates/<int:estimate_id>/delete", methods=["POST"])
def delete_estimate(estimate_id):
    db = get_db()
    db.execute("DELETE FROM estimates WHERE id = ?", (estimate_id,))
    db.commit()
    return redirect(url_for("estimates_page"))


@app.route("/estimate/<int:estimate_id>")
def estimate_detail(estimate_id):
    db = get_db()
    recalc_totals(estimate_id)
    estimate = db.execute("SELECT * FROM estimates WHERE id = ?", (estimate_id,)).fetchone()
    rooms = db.execute("SELECT * FROM rooms WHERE estimate_id = ? ORDER BY id DESC", (estimate_id,)).fetchall()
    contractors = db.execute("SELECT * FROM contractors ORDER BY name").fetchall()
    contractor_works = db.execute("SELECT cw.*, c.name AS contractor_name FROM contractor_works cw JOIN contractors c ON c.id=cw.contractor_id ORDER BY c.name, cw.name").fetchall()
    material_catalog = db.execute("SELECT * FROM material_catalog ORDER BY category, name").fetchall()
    assigned_contractors = db.execute(
        "SELECT ec.id, c.name, c.id AS contractor_id FROM estimate_contractors ec JOIN contractors c ON c.id=ec.contractor_id WHERE ec.estimate_id=? ORDER BY c.name",
        (estimate_id,),
    ).fetchall()

    room_ids = [r["id"] for r in rooms] or [-1]
    placeholders = ",".join("?" for _ in room_ids)
    works = db.execute(f"SELECT w.*, c.name as contractor_name FROM works w LEFT JOIN contractors c ON c.id=w.contractor_id WHERE room_id IN ({placeholders}) ORDER BY w.id DESC", room_ids).fetchall()
    materials = db.execute(f"SELECT * FROM materials WHERE room_id IN ({placeholders}) ORDER BY id DESC", room_ids).fetchall()

    works_by_room = {}
    electric_by_room = {}
    for w in works:
        if w["is_electric"]:
            electric_by_room.setdefault(w["room_id"], []).append(w)
        else:
            works_by_room.setdefault(w["room_id"], []).append(w)

    mats_by_room = {"rough": {}, "finish": {}, "electric": {}}
    for m in materials:
        mats_by_room[m["category"]].setdefault(m["room_id"], []).append(m)

    totals = db.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN w.is_electric=0 THEN w.total ELSE 0 END),0) AS works_total,
          COALESCE(SUM(CASE WHEN w.is_electric=1 THEN w.total ELSE 0 END),0) AS electric_works_total,
          COALESCE(SUM(CASE WHEN m.category='rough' THEN m.total ELSE 0 END),0) AS rough_total,
          COALESCE(SUM(CASE WHEN m.category='finish' THEN m.total ELSE 0 END),0) AS finish_total,
          COALESCE(SUM(CASE WHEN m.category='electric' THEN m.total ELSE 0 END),0) AS electric_material_total
        FROM rooms r
        LEFT JOIN works w ON w.room_id=r.id
        LEFT JOIN materials m ON m.room_id=r.id
        WHERE r.estimate_id=?
        """,
        (estimate_id,),
    ).fetchone()

    room_totals = {}
    for room in rooms:
        rt = db.execute(
            """
            SELECT
              COALESCE((SELECT SUM(total) FROM works WHERE room_id=?),0)+
              COALESCE((SELECT SUM(total) FROM materials WHERE room_id=?),0) AS total
            """,
            (room["id"], room["id"]),
        ).fetchone()
        room_totals[room["id"]] = rt["total"]

    grand_total = totals["works_total"] + totals["electric_works_total"] + totals["rough_total"] + totals["finish_total"] + totals["electric_material_total"]

    return render_template(
        "estimate_detail.html",
        estimate=estimate,
        rooms=rooms,
        works_by_room=works_by_room,
        electric_by_room=electric_by_room,
        mats_by_room=mats_by_room,
        totals=totals,
        room_totals=room_totals,
        grand_total=grand_total,
        contractors=contractors,
        contractor_works=contractor_works,
        material_catalog=material_catalog,
        assigned_contractors=assigned_contractors,
    )


@app.route("/rooms/create", methods=["POST"])
def create_room():
    db = get_db()
    db.execute("INSERT INTO rooms(estimate_id, name, area) VALUES(?,?,?)", (request.form["estimate_id"], request.form["name"], fnum(request.form.get("area"))))
    db.commit()
    return redirect(url_for("estimate_detail", estimate_id=request.form["estimate_id"]))


@app.route("/rooms/<int:room_id>/update", methods=["POST"])
def update_room(room_id):
    db = get_db()
    estimate_id = request.form["estimate_id"]
    db.execute("UPDATE rooms SET name=?, area=? WHERE id=?", (request.form["name"], fnum(request.form.get("area")), room_id))
    db.commit()
    return redirect(url_for("estimate_detail", estimate_id=estimate_id))


@app.route("/rooms/<int:room_id>/delete", methods=["POST"])
def delete_room(room_id):
    db = get_db()
    estimate_id = request.form["estimate_id"]
    db.execute("DELETE FROM rooms WHERE id=?", (room_id,))
    db.commit()
    return redirect(url_for("estimate_detail", estimate_id=estimate_id))


@app.route("/works/create", methods=["POST"])
def create_work():
    db = get_db()
    room_id = int(request.form["room_id"])
    estimate_id = request.form["estimate_id"]
    quantity = fnum(request.form.get("quantity"))
    price = fnum(request.form.get("price"))
    db.execute(
        "INSERT INTO works(room_id, contractor_id, contractor_work_id, name, unit, quantity, price, total, is_electric) VALUES(?,?,?,?,?,?,?,?,?)",
        (room_id, request.form.get("contractor_id") or None, request.form.get("contractor_work_id") or None, request.form["name"], request.form["unit"], quantity, price, quantity * price, int(request.form.get("is_electric", 0))),
    )
    db.commit()
    return redirect(url_for("estimate_detail", estimate_id=estimate_id))


@app.route("/works/<int:work_id>/delete", methods=["POST"])
def delete_work(work_id):
    db = get_db()
    estimate_id = request.form["estimate_id"]
    db.execute("DELETE FROM works WHERE id=?", (work_id,))
    db.commit()
    return redirect(url_for("estimate_detail", estimate_id=estimate_id))


@app.route("/materials/create", methods=["POST"])
def create_material():
    db = get_db()
    quantity = fnum(request.form.get("quantity"))
    price = fnum(request.form.get("price"))
    db.execute(
        "INSERT INTO materials(room_id, category, catalog_material_id, name, quantity, price, total) VALUES(?,?,?,?,?,?,?)",
        (
            request.form["room_id"],
            request.form["category"],
            request.form.get("catalog_material_id") or None,
            request.form["name"],
            quantity,
            price,
            quantity * price,
        ),
    )
    db.commit()
    return redirect(url_for("estimate_detail", estimate_id=request.form["estimate_id"]))


@app.route("/materials/<int:material_id>/delete", methods=["POST"])
def delete_material(material_id):
    db = get_db()
    estimate_id = request.form["estimate_id"]
    db.execute("DELETE FROM materials WHERE id=?", (material_id,))
    db.commit()
    return redirect(url_for("estimate_detail", estimate_id=estimate_id))


@app.route("/ajax/work/<int:work_id>", methods=["POST"])
def ajax_update_work(work_id):
    data = request.get_json(force=True)
    quantity = fnum(data.get("quantity"))
    price = fnum(data.get("price"))
    total = quantity * price
    db = get_db()
    db.execute("UPDATE works SET quantity=?, price=?, total=? WHERE id=?", (quantity, price, total, work_id))
    db.commit()
    return jsonify({"ok": True, "total": total})


@app.route("/ajax/material/<int:material_id>", methods=["POST"])
def ajax_update_material(material_id):
    data = request.get_json(force=True)
    quantity = fnum(data.get("quantity"))
    price = fnum(data.get("price"))
    total = quantity * price
    db = get_db()
    db.execute("UPDATE materials SET quantity=?, price=?, total=? WHERE id=?", (quantity, price, total, material_id))
    db.commit()
    return jsonify({"ok": True, "total": total})


@app.route("/contractors")
def contractors_page():
    db = get_db()
    contractors = db.execute("SELECT * FROM contractors ORDER BY name").fetchall()
    works = db.execute("SELECT cw.*, c.name as contractor_name FROM contractor_works cw JOIN contractors c ON c.id=cw.contractor_id ORDER BY c.name, cw.name").fetchall()
    return render_template("contractors.html", contractors=contractors, works=works)


@app.route("/contractors/create", methods=["POST"])
def create_contractor():
    name = request.form.get("name", "").strip()
    if name:
        db = get_db()
        db.execute("INSERT INTO contractors(name) VALUES(?)", (name,))
        db.commit()
    return redirect(url_for("contractors_page"))


@app.route("/contractor-works/create", methods=["POST"])
def create_contractor_work():
    db = get_db()
    db.execute(
        "INSERT INTO contractor_works(contractor_id, name, unit, default_price) VALUES(?,?,?,?)",
        (request.form["contractor_id"], request.form["name"], request.form["unit"], fnum(request.form.get("default_price"))),
    )
    db.commit()
    return redirect(url_for("contractors_page"))


@app.route("/materials-base")
def materials_base_page():
    materials = get_db().execute("SELECT * FROM material_catalog ORDER BY category, name").fetchall()
    return render_template("materials_base.html", materials=materials)


@app.route("/materials-base/create", methods=["POST"])
def create_material_base():
    db = get_db()
    db.execute(
        "INSERT INTO material_catalog(category, name, unit, default_price) VALUES(?,?,?,?)",
        (request.form["category"], request.form["name"], request.form["unit"], fnum(request.form.get("default_price"))),
    )
    db.commit()
    return redirect(url_for("materials_base_page"))


@app.route("/estimate-contractors/create", methods=["POST"])
def assign_contractor():
    db = get_db()
    db.execute("INSERT INTO estimate_contractors(estimate_id, contractor_id) VALUES(?,?)", (request.form["estimate_id"], request.form["contractor_id"]))
    db.commit()
    return redirect(url_for("estimate_detail", estimate_id=request.form["estimate_id"]))


@app.route("/export/<int:estimate_id>")
def export_csv(estimate_id):
    db = get_db()
    recalc_totals(estimate_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Смета", estimate_id])
    writer.writerow(["Помещение", "Тип", "Наименование", "Ед.", "Кол-во", "Цена", "Сумма", "Подрядчик"])

    rooms = db.execute("SELECT * FROM rooms WHERE estimate_id=? ORDER BY id", (estimate_id,)).fetchall()
    for room in rooms:
        works = db.execute("SELECT w.*, c.name as contractor_name FROM works w LEFT JOIN contractors c ON c.id=w.contractor_id WHERE room_id=?", (room["id"],)).fetchall()
        for w in works:
            writer.writerow([room["name"], "Электрика" if w["is_electric"] else "Работа", w["name"], w["unit"], w["quantity"], w["price"], w["total"], w["contractor_name"] or ""])
        mats = db.execute("SELECT * FROM materials WHERE room_id=?", (room["id"],)).fetchall()
        for m in mats:
            writer.writerow([room["name"], f"Материал:{m['category']}", m["name"], "шт", m["quantity"], m["price"], m["total"], ""])

    mem = io.BytesIO()
    mem.write(output.getvalue().encode("utf-8-sig"))
    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name=f"estimate_{estimate_id}.csv", mimetype="text/csv")


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True)
