// PM2 app definition for PrintQ. Only manages the process named "printq".
// "current" is a symlink to the active release; switching it and reloading deploys a new version.
const root = process.env.PRINTQ_ROOT || "/root/services/printq";
const port = process.env.PRINTQ_PORT || "8010";

module.exports = {
  apps: [
    {
      name: "printq",
      cwd: root + "/current",
      script: root + "/venv/bin/gunicorn",
      interpreter: "none",
      args:
        "config.wsgi:application --bind 0.0.0.0:" + port +
        " --workers 2 --timeout 60 --access-logfile - --error-logfile -",
      env: {
        PATH: root + "/venv/bin:" + (process.env.PATH || ""),
        VIRTUAL_ENV: root + "/venv",
        PYTHONNOUSERSITE: "1",
      },
      autorestart: true,
      max_memory_restart: "300M",
      out_file: root + "/logs/printq-out.log",
      error_file: root + "/logs/printq-error.log",
      merge_logs: true,
      time: true,
    },
  ],
};
