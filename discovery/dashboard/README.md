# Blog Discovery Dashboard

A React app for visualizing blog discovery results.

## Setup

```bash
cd discovery/dashboard
npm install

# Symlink the output data so Vite can serve it
ln -sf ../output public/data
```

## Run

```bash
npm run dev
```

Open http://localhost:5173 in your browser.

## How it works

- Reads `discovery/output/ncaa_mbb_registry.json` via the `/data/` path
- Dropdowns let you filter by sport and team
- Blog URLs are color-coded:
  - 🟢 Green = Accessible & Active
  - 🟡 Amber = Accessible but Outdated  
  - 🔵 Blue = Accessible (recency unknown)
  - 🔴 Red = Inaccessible (with error label)
