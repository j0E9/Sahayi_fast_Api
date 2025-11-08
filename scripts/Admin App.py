"""
Sahayi Admin Panel — Modern Dark UI (PyQt6)
This file replaces the prior admin panel UI with a modern dark-themed layout.
Image panels correctly hide/show based on the selected mode and the redundant refresh
button was removed from the action bar.
"""

import sys
import requests
import threading
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QLabel,
    QPushButton, QTextEdit, QMessageBox, QFrame, QSizePolicy, QSplitter,
    QLineEdit, QStackedWidget, QScrollArea, QGridLayout
)
from PyQt6.QtGui import QPixmap, QFont, QAction
from PyQt6.QtCore import Qt, pyqtSignal, QObject

API_BASE = "http://127.0.0.1:8000"


class AdminKYC(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sahayi Admin Panel")
        self.resize(1200, 760)
        self.mode = "pending"  # pending, users, workers, jobs, bookings, verification

        # ---- dark stylesheet (modern look) ----
        self.setStyleSheet("""
        QWidget { background-color: #0f1115; color: #d6d6d6; font-family: 'Segoe UI', Arial; }
        QFrame#sidebar { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #121317, stop:1 #0e1014); border-right: 1px solid #222; }
        QLabel#title { font-size: 20px; font-weight: 600; color: #ffffff; }
        QLabel#subtitle { color: #9aa3b2; font-size: 12px; }
        QPushButton#navBtn { text-align: left; padding: 10px 14px; border-radius: 8px; color: #cfe8dc; background: transparent; }
        QPushButton#navBtn:hover { background: #172027; color: #fff; }
        QPushButton#navBtn[active="true"] { background: #10171b; border-left: 4px solid #19d18b; color: #fff; }
        QListWidget { background: transparent; border: none; }
        QTextEdit { background: #0f1316; border: 1px solid #1e2528; color: #e8eef0; padding: 8px; border-radius: 8px; }
        QPushButton.action { border-radius: 8px; padding: 8px 12px; font-weight: 600; color: white; }
        QPushButton#approve { background: #28a745; }
        QPushButton#deny { background: #d9534f; }
        QPushButton#suspend { background: #ff8c00; }
        QPushButton#reactivate { background: #6c757d; }
        QPushButton#wallet { background: #1976d2; }
        QPushButton#refresh { background: #0db2a0; color: white; font-weight: 600; }
        QLineEdit#token { background: #0f1316; border: 1px solid #1e2528; padding: 6px; color: #cfe8dc; border-radius: 6px; }
        QLabel.section { font-size: 13px; color: #cfe8dc; }
        QLabel.small { font-size: 11px; color: #9aa3b2; }
        """)

        # ---- top-level layout: horizontal splitter ----
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        layout = QHBoxLayout(self)
        layout.addWidget(main_splitter)

        # === left sidebar ===
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(220)
        side_v = QVBoxLayout(sidebar)
        side_v.setContentsMargins(12, 12, 12, 12)
        side_v.setSpacing(10)

        # Logo / Title at top
        lbl_title = QLabel("Sahayi Admin")
        lbl_title.setObjectName("title")
        side_v.addWidget(lbl_title)
        side_v.addSpacing(6)
        side_v.addWidget(QLabel("Admin Control Panel"))
        side_v.addSpacing(12)

        # Navigation buttons (vertical)
        self.nav_buttons = {}
        nav_names = [("Pending KYC", "pending"),
                     ("Users", "users"),
                     ("Workers", "workers"),
                     ("Jobs", "jobs"),
                     ("Bookings", "bookings")]
        for label, mode in nav_names:
            btn = QPushButton(label)
            btn.setObjectName("navBtn")
            btn.setProperty("active", False)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _, m=mode: self.switch_mode(m))
            side_v.addWidget(btn)
            self.nav_buttons[mode] = btn

        side_v.addStretch(1)

        # Token input
        token_row = QHBoxLayout()
        self.token_input = QLineEdit()
        self.token_input.setObjectName("token")
        self.token_input.setPlaceholderText("Admin API token (paste here)")
        self.set_token_btn = QPushButton("Set")
        self.set_token_btn.clicked.connect(self.on_set_token)
        token_row.addWidget(self.token_input)
        token_row.addWidget(self.set_token_btn)
        side_v.addLayout(token_row)

        main_splitter.addWidget(sidebar)

        # === right area (main content) ===
        content_frame = QFrame()
        content_v = QVBoxLayout(content_frame)
        content_v.setContentsMargins(16, 12, 16, 12)
        content_v.setSpacing(12)

        # Top header row: title + small hint
        header_row = QHBoxLayout()
        self.header_label = QLabel("KYC — Pending")
        self.header_label.setObjectName("title")
        header_row.addWidget(self.header_label)
        header_row.addStretch(1)
        content_v.addLayout(header_row)

        # Middle split: left list and right details
        mid_splitter = QSplitter(Qt.Orientation.Horizontal)
        # left list panel (scrollable)
        left_panel = QFrame()
        left_panel_layout = QVBoxLayout(left_panel)
        left_panel_layout.setContentsMargins(8, 8, 8, 8)
        left_panel_layout.setSpacing(8)

        # small subtitle
        left_panel_layout.addWidget(QLabel("Requests / Items"))
        self.listw = QListWidget()
        self.listw.setMaximumWidth(360)
        self.listw.currentRowChanged.connect(self.on_select)
        left_panel_layout.addWidget(self.listw)
        left_panel_layout.addSpacing(6)

        # refresh button
        self.refresh_btn = QPushButton("🔄 Refresh List")
        self.refresh_btn.setObjectName("refresh") # Give it the ID for styling
        self.refresh_btn.clicked.connect(self.load_users)
        left_panel_layout.addWidget(self.refresh_btn)

        mid_splitter.addWidget(left_panel)

        # right detail panel (card-like)
        right_panel = QFrame()
        right_panel_layout = QVBoxLayout(right_panel)
        right_panel_layout.setContentsMargins(12, 12, 12, 12)
        right_panel_layout.setSpacing(10)
        # card header
        card_title = QLabel("Details")
        card_title.setProperty("class", "section")
        right_panel_layout.addWidget(card_title)

        # info text area
        self.info = QTextEdit()
        self.info.setReadOnly(True)
        right_panel_layout.addWidget(self.info, 2)

        # ==================================
        #  CHANGE 1: Wrap images in a QWidget
        # ==================================
        self.image_area_widget = QWidget()
        imgs_row = QHBoxLayout(self.image_area_widget) # Layout is on the widget
        imgs_row.setContentsMargins(0, 0, 0, 0)
        
        # Aadhaar
        aad_box = QVBoxLayout()
        aad_label = QLabel("Aadhaar / ID")
        aad_label.setProperty("class", "small")
        aad_box.addWidget(aad_label)
        self.aadhaar_img = QLabel("No file")
        self.aadhaar_img.setFixedSize(320, 200)
        self.aadhaar_img.setStyleSheet("border: 1px solid #1e2528; background: #0f1316;")
        self.aadhaar_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.aadhaar_img.setScaledContents(True)
        aad_box.addWidget(self.aadhaar_img)
        imgs_row.addLayout(aad_box)

        # Live photo
        live_box = QVBoxLayout()
        live_label = QLabel("Live Photo")
        live_label.setProperty("class", "small")
        live_box.addWidget(live_label)
        self.live_img = QLabel("No file")
        self.live_img.setFixedSize(320, 200)
        self.live_img.setStyleSheet("border: 1px solid #1e2528; background: #0f1316;")
        self.live_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.live_img.setScaledContents(True)
        live_box.addWidget(self.live_img)
        imgs_row.addLayout(live_box)

        # Add the single widget to the layout
        right_panel_layout.addWidget(self.image_area_widget)

        # actions row
        actions_row = QHBoxLayout()
        actions_row.addStretch(1)
        self.approve_btn = QPushButton("Approve")
        self.approve_btn.setObjectName("approve")
        self.approve_btn.setProperty("class", "action")
        self.deny_btn = QPushButton("Deny")
        self.deny_btn.setObjectName("deny")
        self.suspend_btn = QPushButton("Suspend")
        self.suspend_btn.setObjectName("suspend")
        self.reactivate_btn = QPushButton("Reactivate")
        self.reactivate_btn.setObjectName("reactivate")
        self.view_wallet_btn = QPushButton("View Wallet")
        self.view_wallet_btn.setObjectName("wallet")

        for b in (self.approve_btn, self.deny_btn, self.suspend_btn, self.reactivate_btn, self.view_wallet_btn):
            b.setFixedHeight(36)
            b.setProperty("class", "action") # Ensure all get action class
            actions_row.addWidget(b)
        actions_row.addStretch(1)
        right_panel_layout.addLayout(actions_row)

        mid_splitter.addWidget(right_panel)
        mid_splitter.setSizes([360, 820])

        content_v.addWidget(mid_splitter, 1)
        main_splitter.addWidget(content_frame)
        main_splitter.setSizes([260, 920])

        # connect events for actions
        self.approve_btn.clicked.connect(self.on_approve)
        self.deny_btn.clicked.connect(self.on_deny)
        self.suspend_btn.clicked.connect(self.on_suspend)
        self.reactivate_btn.clicked.connect(self.on_reactivate)
        self.view_wallet_btn.clicked.connect(self.on_view_wallet)

        # internal state
        self.users = []
        self.api_token = None

        # worker fetcher (background)
        class WorkerFetcher(QObject):
            finished = pyqtSignal(object)

            def fetch(self, job_id, headers):
                threading.Thread(target=self._do, args=(job_id, headers), daemon=True).start()

            def _do(self, job_id, headers):
                url = f"{API_BASE}/admin/job/{job_id}/workers"
                try:
                    resp = requests.get(url, headers=headers, timeout=6)
                    if resp.status_code == 200:
                        self.finished.emit({"ok": True, "data": resp.json()})
                        return
                    else:
                        try:
                            msg = resp.json()
                        except Exception:
                            msg = resp.text
                        self.finished.emit({"ok": False, "status": resp.status_code, "error": msg})
                        return
                except Exception as e:
                    self.finished.emit({"ok": False, "error": str(e)})

            def fetch_by_title(self, title, headers):
                threading.Thread(target=self._do_by_title, args=(title, headers), daemon=True).start()

            def _do_by_title(self, title, headers):
                try:
                    params = {"title": title}
                    url = f"{API_BASE}/admin/job/workers_by_title"
                    resp = requests.get(url, headers=headers, params=params, timeout=8)
                    if resp.status_code == 200:
                        self.finished.emit({"ok": True, "data": resp.json()})
                        return
                    else:
                        try:
                            msg = resp.json()
                        except Exception:
                            msg = resp.text
                        self.finished.emit({"ok": False, "status": resp.status_code, "error": msg})
                        return
                except Exception as e:
                    self.finished.emit({"ok": False, "error": str(e)})

        self.worker_fetcher = WorkerFetcher()
        self.worker_fetcher.finished.connect(self.on_workers_fetched)

        # start in pending mode
        self.switch_mode("pending")

    # ---- data loader ----
    def load_users(self):
        try:
            if self.mode == "pending":
                url = f"{API_BASE}/admin/kyc/pending?status=pending"
            elif self.mode == "workers":
                url = f"{API_BASE}/admin/kyc/pending?status=approved"
            elif self.mode == "users":
                url = f"{API_BASE}/admin/users"
            elif self.mode == "jobs":
                url = f"{API_BASE}/admin/jobs"
            elif self.mode == "bookings":
                url = f"{API_BASE}/admin/bookings"
            elif self.mode == "verification":
                url = f"{API_BASE}/admin/kyc/pending?status=pending"
            else:
                url = f"{API_BASE}/admin/kyc/pending?status=pending"

            resp = requests.get(url, headers=self._headers(), timeout=6)

            if resp.status_code == 404:
                QMessageBox.information(self, "Not Implemented", f"Endpoint not available:\n{url}")
                self.users = []
                return
            if resp.status_code == 401:
                try:
                    server_msg = resp.json().get('detail', resp.text)
                except Exception:
                    server_msg = resp.text
                QMessageBox.critical(self, "Unauthorized (401)", f"{server_msg}\n\nPaste valid Admin token.")
                self.users = []
                return
            if resp.status_code == 403:
                try:
                    server_msg = resp.json().get('detail', resp.text)
                except Exception:
                    server_msg = resp.text
                QMessageBox.critical(self, "Forbidden (403)", f"{server_msg}\n\nEnsure ADMIN_API_TOKEN is configured on server.")
                self.users = []
                return
            resp.raise_for_status()
            raw = resp.json()

            # If jobs mode, aggregate unique titles (case-insensitive)
            if self.mode == "jobs":
                grouped = {}
                for j in raw:
                    title = (j.get('title') or '').strip()
                    key = title.lower()
                    if key not in grouped:
                        grouped[key] = {
                            "title": title,
                            "job_ids": [],
                            "description": j.get('description'),
                            "status": j.get('status'),
                            "rate": j.get('rate'),
                            "rate_type": j.get('rate_type')
                        }
                    grouped[key]['job_ids'].append(j.get('id'))
                aggregated = []
                for k, v in grouped.items():
                    aggregated.append(v)
                self.users = aggregated
            else:
                self.users = raw

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to fetch data:\n{e}")
            self.users = []

        # update UI
        self.listw.clear()
        self.info.clear()
        self.aadhaar_img.setPixmap(QPixmap())
        self.aadhaar_img.setText("No file")
        self.live_img.setPixmap(QPixmap())
        self.live_img.setText("No file")

        for u in self.users:
            if self.mode in ("pending", "verification"):
                status = (u.get('kyc_status') or 'unknown').capitalize()
                label = f"{u.get('id')} — {u.get('name')} ({status})"
            elif self.mode == "workers":
                label = f"{u.get('id')} — {u.get('name')} (worker)"
            elif self.mode == "jobs":
                # show title and number of postings
                num = len(u.get('job_ids', []))
                label = f"{u.get('title')}  \u00A0({num})"
            elif self.mode == "bookings":
                label = f"{u.get('id')} — Booking ({u.get('status', '')})"
            else:
                label = f"{u.get('id')} — {u.get('name')}"
            self.listw.addItem(label)

        # adjust action buttons visibility
        is_pending = self.mode == "pending"
        is_workers = self.mode == "workers"
        is_users = self.mode == "users"
        self.approve_btn.setVisible(is_pending)
        self.deny_btn.setVisible(is_pending)
        self.suspend_btn.setVisible(is_workers)
        self.reactivate_btn.setVisible(is_workers)
        self.view_wallet_btn.setVisible(is_users)

    # ---- selection handler ----
    def on_select(self, idx):
        if idx < 0 or idx >= len(self.users):
            self.info.clear()
            return
        u = self.users[idx]
        # build details based on mode
        lines = []
        if self.mode == "jobs":
            lines.append(f"📌 Title: {u.get('title')}")
            lines.append(f"🧾 Postings: {len(u.get('job_ids', []))}")
            lines.append(f"📄 Description: {u.get('description') or ''}")
            lines.append(f"💲 Rate: {u.get('rate') or 'N/A'} {u.get('rate_type') or ''}")
            self.info.setPlainText("\n".join(lines) + "\n\nLoading workers for this job...")
            # fetch workers by title
            try:
                self.worker_fetcher.fetch_by_title(u.get('title'), self._headers())
            except Exception:
                self.info.setPlainText("\n".join(lines) + "\n\nUnable to fetch workers.")
            # This 'return' is correct, as jobs don't have images
            return

        # for user/worker/kyc entries
        lines.append(f"🆔 ID: {u.get('id')}")
        lines.append(f"👤 Name: {u.get('name')}")
        if u.get('email'):
            lines.append(f"📧 Email: {u.get('email')}")
        if u.get('phone'):
            lines.append(f"📞 Phone: {u.get('phone')}")
        if u.get('verified_name'):
            lines.append(f"🪪 Verified Name: {u.get('verified_name')}")
        if u.get('kyc_status'):
            lines.append(f"📊 KYC Status: {u.get('kyc_status')}")
        self.info.setPlainText("\n".join(lines))

        # load images (this will only be hit for non-job modes)
        self._load_image(u.get("aadhaar_file"), self.aadhaar_img)
        self._load_image(u.get("live_photo"), self.live_img)

    # ---- worker fetched callback ----
    def on_workers_fetched(self, payload):
        if not payload.get('ok'):
            err = payload.get('error') or payload.get('status')
            current = self.info.toPlainText()
            self.info.setPlainText(current + f"\n\nFailed to load workers: {err}")
            return
        data = payload.get('data', {})
        workers = data.get('workers', [])
        current = self.info.toPlainText()
        if not workers:
            self.info.setPlainText(current + "\n\nNo workers registered for this job.")
            return
        lines = ["\nWorkers registered for this job:"]
        for w in workers:
            lines.append(f"- {w.get('name')} (id:{w.get('id')}) — {w.get('phone') or 'no-phone'}")
        self.info.setPlainText(current + "\n".join(lines))

    # ---- helper load image ----
    def _load_image(self, urlpath, widget: QLabel):
        if not urlpath:
            widget.setText("No file")
            widget.setPixmap(QPixmap())
            return
        url = API_BASE + urlpath
        try:
            r = requests.get(url, headers=self._headers(), timeout=6)
            r.raise_for_status()
            pix = QPixmap()
            pix.loadFromData(r.content)
            widget.setPixmap(pix)
        except Exception:
            widget.setText("Failed to load")

    # ---- approve/deny ----
    def _post_status(self, user_id, status):
        try:
            url = f"{API_BASE}/admin/kyc/update_status"
            resp = requests.post(url, json={"user_id": user_id, "status": status}, headers=self._headers(), timeout=6)
            if resp.status_code == 404:
                QMessageBox.information(self, "Not Implemented", f"Endpoint not available:\n{url}")
                return
            if resp.status_code in (401, 403):
                try:
                    server_msg = resp.json().get('detail', resp.text)
                except Exception:
                    server_msg = resp.text
                QMessageBox.critical(self, f"Auth Error ({resp.status_code})", f"{server_msg}\n\nEnsure ADMIN_API_TOKEN is configured and pasted here.")
                return
            resp.raise_for_status()
            QMessageBox.information(self, "Success", f"User {user_id} set to {status}.")
            self.load_users()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to update status:\n{e}")

    def on_approve(self):
        idx = self.listw.currentRow()
        if idx < 0:
            return
        uid = self.users[idx].get("id")
        self._post_status(uid, "approved")

    def on_deny(self):
        idx = self.listw.currentRow()
        if idx < 0:
            return
        uid = self.users[idx].get("id")
        self._post_status(uid, "denied")

    # ---- worker admin actions (placeholders if endpoints exist) ----
    def on_suspend(self):
        idx = self.listw.currentRow()
        if idx < 0:
            return
        uid = self.users[idx].get("id")
        try:
            url = f"{API_BASE}/admin/worker/suspend"
            resp = requests.post(url, json={"user_id": uid}, headers=self._headers(), timeout=6)
            if resp.status_code == 404:
                QMessageBox.information(self, "Not Implemented", f"Endpoint not available:\n{url}")
                return
            resp.raise_for_status()
            QMessageBox.information(self, "Success", f"Worker {uid} suspended.")
            self.load_users()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to suspend worker:\n{e}")

    def on_reactivate(self):
        idx = self.listw.currentRow()
        if idx < 0:
            return
        uid = self.users[idx].get("id")
        try:
            url = f"{API_BASE}/admin/worker/reactivate"
            resp = requests.post(url, json={"user_id": uid}, headers=self._headers(), timeout=6)
            if resp.status_code == 404:
                QMessageBox.information(self, "Not Implemented", f"Endpoint not available:\n{url}")
                return
            resp.raise_for_status()
            QMessageBox.information(self, "Success", f"Worker {uid} reactivated.")
            self.load_users()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to reactivate worker:\n{e}")

    # ---- wallet/view/delete ----
    def on_view_wallet(self):
        idx = self.listw.currentRow()
        if idx < 0:
            return
        uid = self.users[idx].get("id")
        try:
            url = f"{API_BASE}/admin/user/{uid}/wallet"
            resp = requests.get(url, headers=self._headers(), timeout=6)
            if resp.status_code == 404:
                QMessageBox.information(self, "Not Implemented", f"Endpoint not available:\n{url}")
                return
            resp.raise_for_status()
            data = resp.json()
            lines = [f"Balance: {data.get('wallet_balance')}"]
            lines.append("\nRecent transactions:")
            for t in data.get('transactions', [])[:8]:
                lines.append(f"- {t.get('created_at')} {t.get('kind')} {t.get('amount')}")
            QMessageBox.information(self, f"Wallet {uid}", "\n".join(lines))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to fetch wallet:\n{e}")

    def on_delete_user(self):
        idx = self.listw.currentRow()
        if idx < 0:
            return
        uid = self.users[idx].get("id")
        rv = QMessageBox.question(self, "Confirm Delete", f"Delete user {uid}? This will remove the user.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if rv != QMessageBox.StandardButton.Yes:
            return
        try:
            url = f"{API_BASE}/admin/user/{uid}"
            resp = requests.delete(url, headers=self._headers(), timeout=6)
            if resp.status_code == 404:
                QMessageBox.information(self, "Not Found", f"Endpoint not found:\n{url}")
                return
            resp.raise_for_status()
            QMessageBox.information(self, "Deleted", f"User {uid} deleted.")
            self.load_users()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete user:\n{e}")

    # ---- mode switcher ----
    def switch_mode(self, mode: str):
        self.mode = mode
        # set active nav style
        for m, btn in self.nav_buttons.items():
            btn.setProperty("active", m == mode)
            btn.setStyle(btn.style())  # force style update
        # update header label
        header_map = {
            "pending": "KYC — Pending",
            "users": "Users",
            "workers": "Workers",
            "jobs": "Jobs",
            "bookings": "Bookings",
            "verification": "Verification"
        }
        self.header_label.setText(header_map.get(mode, "Admin Panel"))

        # ==================================
        #  CHANGE 2: Show/Hide image area
        # ==================================
        # Only show images for modes that involve user verification
        if mode in ("pending", "workers", "verification"):
            self.image_area_widget.setVisible(True)
        else:
            self.image_area_widget.setVisible(False)
        
        self.load_users()

    # ---- token helpers ----
    def on_set_token(self):
        token = self.token_input.text().strip()
        self.api_token = token or None
        if self.api_token:
            QMessageBox.information(self, "Token set", "API token saved in memory for this session.")
        else:
            QMessageBox.information(self, "Token cleared", "API token cleared.")

    def _headers(self):
        headers = {}
        if getattr(self, "api_token", None):
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers


def main():
    app = QApplication(sys.argv)
    win = AdminKYC()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()