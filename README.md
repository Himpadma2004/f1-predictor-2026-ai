# F1 PREDICTOR 2026

**Broadcast-grade F1 dashboard with live telemetry streaming and AI-powered race predictions.**

A next-generation Formula 1 analytics platform built for the 2026 regulation era, featuring real-time telemetry visualization, Gemini-powered news analysis, and LSTM/RNN predictive models.

## 🏁 Key Features

### Real-Time Telemetry
- **Live Telemetry Dashboard**: OpenF1 integration with 3.7Hz polling (every 200ms)
- **Interactive Track Map**: SVG track visualization with animated driver positions
- **Sector Timing Analysis**: Real-time lap sector breakdowns with delta comparisons
- **Pedal Input Visualization**: Throttle/brake analysis with Recharts

### Broadcast Replay
- **Tom Shaw Engine**: Ghost lap comparison for competitive analysis
- **Interactive Seeker**: Video-style replay control with play/pause
- **Multi-Driver Comparison**: Head-to-head telemetry overlays

### AI Predictions
- **Win Probability Gauges**: Neural network predictions for race outcomes
- **Overtake Success Rates**: Real-time probability of successful passes
- **Safety Car Risk Assessment**: Tactical recommendations
- **Pit Window Optimization**: Ideal pit entry/exit timing

### News Sentinel
- **Gemini AI Analysis**: Headlines scored for team performance impact (-1.0 to +1.0)
- **Real-time Sentiment**: Positive/neutral/negative classification
- **Team-specific Filtering**: News feed by constructor or driver
- **High-density Feed**: Top impactful articles prioritized

## 📁 Project Structure

```
F1/
├── src/                    # Python backend
│   ├── utils/
│   │   └── config.py      # Environment & config management
│   ├── models/
│   │   ├── telemetry.py   # Pydantic models for telemetry data
│   │   └── predictions.py # AI model output schemas
│   ├── ingestion/
│   │   ├── openf1_client.py       # Live telemetry (3.7Hz)
│   │   ├── jolpica_client.py      # Historical race data
│   │   └── news_analyzer.py       # Gemini news scoring
│   ├── services/          # Business logic
│   └── api/              # Future API routes
├── main.py               # FastAPI app (root)
├── frontend/            # Next.js 14 app
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx     # Command Hub (home)
│   │   ├── live/        # Live Race Center
│   │   ├── telemetry/   # Telemetry Pro
│   │   └── predictor/   # AI Predictor
│   ├── components/
│   │   ├── stitch/      # Stitch card system
│   │   └── common/      # Shared components
│   ├── lib/api.ts       # Backend API client
│   └── store/           # Zustand state management
├── data/
│   ├── raw/            # JSON data cache
│   └── processed/      # Parquet files
├── requirements.txt    # Python dependencies
├── .env.example        # Environment template
└── README.md
```

## 🚀 Quick Start

### Backend Setup

1. **Create environment file**:
   ```bash
   cp .env.example .env
   # Fill in your API keys:
   # - GEMINI_API_KEY (from Google AI Studio)
   # - NEWS_API_KEY (from newsapi.org)
   ```

2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run backend**:
   ```bash
   python main.py
   # API available at http://localhost:8000
   # Docs at http://localhost:8000/docs
   ```

### Frontend Setup

1. **Install dependencies**:
   ```bash
   cd frontend
   npm install
   ```

2. **Create .env.local** (optional):
   ```bash
   NEXT_PUBLIC_API_URL=http://localhost:8000
   ```

3. **Run frontend**:
   ```bash
   npm run dev
   # UI available at http://localhost:3000
   ```

## 🔌 API Endpoints

### Telemetry
- `GET /api/v1/telemetry/live` - Current driver positions
- `GET /api/v1/telemetry/driver/{driver_number}` - Latest telemetry for driver
- `GET /api/v1/telemetry/history` - Historical telemetry buffer

### News
- `GET /api/v1/news` - Latest news with Gemini scoring
- `POST /api/v1/news/refresh` - Trigger news update

### Race Data
- `GET /api/v1/races/{year}` - Season races
- `GET /api/v1/results/{year}/{round}` - Race results
- `GET /api/v1/standings/{year}` - Championship standings

### Predictions
- `GET /api/v1/predict/live` - Overtake probability comparison
- `GET /api/v1/predict/{driver}` - Driver prediction caliber
- `POST /api/v1/predict/model_input` - ML model input tensor

## 📊 Data Sources

| Source | Purpose | Update Rate |
|--------|---------|------------|
| **OpenF1** | Live telemetry | 200ms (3.7Hz) |
| **Jolpica** | Historical races | On-demand (cached) |
| **NewsAPI** | F1 headlines | Hourly |
| **Gemini 1.5 Flash** | Sentiment analysis | Per article |

## 🎨 Design System

### Stitch Card Components
Every module follows the Google Stitch philosophy:
- 1px cyan border with glass morphism
- 12px corner radius (`.stitch-card`, `rounded-stitch`)
- 20px backdrop blur (`.backdrop-blur-glass`)
- Smooth hover states with opacity transitions

### Theme Colors
- **Obsidian**: `#050505` / `#080808` (background)
- **Cyan**: `#00D2BE` / `#00F2FF` (accent, data)
- **F1 Red**: `#E10600` / `#FF0000` (alert, highlight)
- **Neutral**: `#2d2d2d` to `#0a0a0a` (gradients)

### Typography
- **Headlines**: Formula1-Display-Bold (custom import)
- **Data**: JetBrains Mono (monospace, zero layout shift)

## 🧠 ML-Ready Architecture

All code is designed for future LSTM/RNN ingestion:

### Normalization
- **Speed**: 0-360 km/h → 0.0-1.0
- **RPM**: 0-15500 → 0.0-1.0
- **Throttle/Brake**: 0-100% → 0.0-1.0
- **Gear**: 0-8 → normalized

### Model Inputs
- 7-feature telemetry vector per timestamp
- High-precision Unix timestamps for sequence learning
- Parquet caching for fast training data loading

## 🔒 Environment Variables

```env
# Required
GEMINI_API_KEY=<your-gemini-key>
NEWS_API_KEY=<your-newsapi-key>

# Optional (defaults shown)
OPENF1_BASE_URL=https://api.openf1.org/v1
JOLPICA_BASE_URL=http://api.jolpi.ca/ergast/f1
FRONTEND_URL=http://localhost:3000
DEBUG=False
LOG_LEVEL=INFO
```

## 🛠 Development

### Backend
- **Framework**: FastAPI with async/await
- **Type Safety**: Pydantic v2
- **Testing**: pytest
- **Linting**: flake8, black, mypy

### Frontend
- **Framework**: Next.js 14 (App Router)
- **Styling**: Tailwind CSS v3 + custom CSS
- **State**: Zustand (lightweight Redux alternative)
- **Charts**: Recharts
- **Icons**: Lucide React

## 📈 Performance

- **Backend**: ~200ms response time for telemetry polling
- **Frontend**: 60 FPS animations with Framer Motion
- **Caching**: Parquet files for historical data (instant load)
- **Real-time**: WebSocket-ready for future live streaming

## 🎯 2026 Regulation Features

- 768kg weight (30kg reduction) tracking
- 50/50 ICE/MGU-K power split visualization
- Active Aero state (X-Mode/Z-Mode) display
- Manual electrical boost overlay
- DRS to Active Aero transition handling

## 📝 Notes

- All telemetry timestamps use ISO 8601 with millisecond precision
- Predictions are currently mock data (ready for ML model integration)
- News sentiment analysis uses Gemini 1.5 Flash for cost efficiency
- Track coordinates are normalized to 0-1 range for SVG rendering

## 🚀 Next Steps

1. **Deploy**: Docker containerization for backend + Vercel for frontend
2. **Database**: Add PostgreSQL for telemetry history storage
3. **WebSockets**: Real-time streaming instead of polling
4. **ML Models**: Integrate trained LSTM for live predictions
5. **Authentication**: Implement JWT for team access zones

## 📄 License

F1 Predictor 2026 © 2026. Proprietary.

---

**Built with ❤️ for the 2026 Formula 1 season**
