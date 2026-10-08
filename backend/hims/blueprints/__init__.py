def register_blueprints(app):
    from . import (admin, announcements, attendance, auth, creative, dashboard, departments, dept_requests, documents,
                   campaigns, employees, files, finance, helpdesk, leaves, messages, notifications, people, pipelines, projects, reports,
                   rights, search, tasks, vendor_products, vendors)

    for module in (auth, dashboard, employees, departments, tasks, attendance, leaves, helpdesk, finance,
                   vendors, creative, documents, announcements, notifications, search, reports, admin, messages, dept_requests, rights, projects, campaigns,
                   vendor_products, files, people):
        app.register_blueprint(module.bp)
    app.register_blueprint(pipelines.leads_bp)
    app.register_blueprint(pipelines.recruitment_bp)
    app.register_blueprint(pipelines.incubation_bp)
