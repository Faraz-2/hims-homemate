"""Start HIMS.  On the server laptop:  python run.py"""
import os

from hims import create_app
from hims.services import lan_ip

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HIMS_HOST", "0.0.0.0")   # 0.0.0.0 = reachable from other laptops on the LAN
    port = int(os.environ.get("HIMS_PORT", "8000"))
    print("=" * 62)
    print("  Home Mate HIMS is starting")
    print(f"  On this computer : http://localhost:{port}")
    print(f"  From other laptops: http://{lan_ip()}:{port}")
    print("  Stop the server with Ctrl+C")
    print("=" * 62)
    from hims.backup_vault import start_scheduler
    start_scheduler(app)          # complete (department / user / date) backup once a day, see Administration > Backups
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except ImportError:  # waitress missing: fall back to Flask's development server
        app.run(host=host, port=port)
