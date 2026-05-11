-- Schema for subscribers.db
-- Exported 2026-05-11T16:59:40.202457

CREATE TABLE batch_comparisons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            candidate_symbols TEXT NOT NULL,
            status TEXT DEFAULT 'STARTED',
            completed_at TIMESTAMP,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE decision_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            price_at_decision REAL NOT NULL,
            drop_percent REAL NOT NULL,
            recommendation TEXT NOT NULL,
            reasoning TEXT,
            status TEXT DEFAULT 'Ignored',
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            company_name TEXT,
            pe_ratio REAL,
            market_cap REAL,
            sector TEXT,
            region TEXT,
            is_earnings_drop BOOLEAN DEFAULT 0,
            earnings_date TEXT,
            ai_score REAL,
            deep_research_verdict TEXT,
            deep_research_risk TEXT,
            deep_research_catalyst TEXT,
            deep_research_knife_catch TEXT,
            deep_research_score INTEGER,
            deep_research_swot TEXT,
            deep_research_global_analysis TEXT,
            deep_research_local_analysis TEXT
        , git_version TEXT, entry_price_low REAL, entry_price_high REAL, stop_loss REAL, take_profit_1 REAL, take_profit_2 REAL, pre_drop_price REAL, upside_percent REAL, downside_risk_percent REAL, risk_reward_ratio REAL, drop_type TEXT, conviction TEXT, entry_trigger TEXT, reassess_in_days INTEGER, sell_price_low REAL, sell_price_high REAL, ceiling_exit REAL, exit_trigger TEXT, deep_research_review_verdict TEXT, deep_research_action TEXT, deep_research_conviction TEXT, deep_research_entry_low REAL, deep_research_entry_high REAL, deep_research_stop_loss REAL, deep_research_tp1 REAL, deep_research_tp2 REAL, deep_research_upside REAL, deep_research_downside REAL, deep_research_rr_ratio REAL, deep_research_drop_type TEXT, deep_research_entry_trigger TEXT, deep_research_verification TEXT, deep_research_blindspots TEXT, deep_research_reason TEXT, deep_research_sell_price_low REAL, deep_research_sell_price_high REAL, deep_research_ceiling_exit REAL, deep_research_exit_trigger TEXT, reassess_sell_action TEXT, reassess_thesis_status TEXT, reassess_sell_price_low REAL, reassess_sell_price_high REAL, reassess_ceiling_exit REAL, reassess_updated_stop_loss REAL, reassess_exit_trigger TEXT, reassess_timestamp TEXT, reassess_reasoning TEXT, data_depth TEXT, batch_winner BOOLEAN DEFAULT 0, batch_id INTEGER, gatekeeper_tier TEXT, reported_eps REAL, consensus_eps REAL, surprise_pct REAL, earnings_fiscal_quarter TEXT, earnings_narrative_flag TEXT, sa_quant_rating REAL, sa_authors_rating REAL, wall_street_rating REAL, sa_rank INTEGER);

CREATE TABLE decision_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER NOT NULL,
            price REAL NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (decision_id) REFERENCES decision_points (id)
        );

CREATE TABLE desk_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_point_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                position_size REAL NOT NULL,
                attractiveness_score REAL NOT NULL,

                stop_loss_price REAL,
                take_profit_target REAL,
                trailing_stop_pct REAL,
                max_hold_days INTEGER,
                exit_triggers TEXT,

                high_water_mark REAL,
                last_reviewed_at TEXT,
                current_price REAL,
                unrealized_pnl_pct REAL,

                exit_date TEXT,
                exit_price REAL,
                realized_pnl_pct REAL,
                exit_reason TEXT
            , entry_price_source TEXT, entry_spy_price REAL, exit_spy_price REAL);

CREATE TABLE desk_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                desk_position_id INTEGER NOT NULL,
                review_date TEXT NOT NULL,
                price_at_review REAL NOT NULL,
                unrealized_pnl_at_review REAL NOT NULL,
                days_held INTEGER NOT NULL,
                triggers_checked TEXT,
                verdict TEXT NOT NULL,
                conviction_score INTEGER NOT NULL,
                adjustment_details TEXT,
                sensor_reports TEXT,
                debate_summary TEXT,
                pm_reasoning TEXT,
                deep_research_override INTEGER DEFAULT 0,
                deep_research_reasoning TEXT, review_type TEXT NOT NULL DEFAULT 'COUNCIL_DEEP', escalation_trigger TEXT, escalation_reason TEXT,
                FOREIGN KEY (desk_position_id) REFERENCES desk_positions(id)
            );

CREATE TABLE subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE transcript_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            fiscal_quarter TEXT NOT NULL,
            source TEXT NOT NULL,
            text TEXT NOT NULL,
            report_date TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, fiscal_quarter)
        );

