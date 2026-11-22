"""Configuration defaults and safe Garmin field allowlists."""
DEFAULT_CONFIG = {
    'timezone': 'America/Chicago',
    'baseline_days': 30,
    'analysis_days': 7,
    'resting_hr_bpm': None,
    'usual_bedtime': None,
    'usual_wake_time': None,
    'ai_reports_enabled': False,
    'openai_model': 'gpt-4.1-mini',
    'weight_kg': None,
    'race_goal': '',
    'dietary_preferences': '',
    'feedback_reporting_mode': 'issues_only',
}
ACTIVITY_KEYS = ('activityId','activityName','startTimeLocal','startTimeGMT',
                 'duration','distance','calories','averageHR','maxHR',
                 'averageSpeed','maxSpeed','averageRunningCadenceInStepsPerMinute',
                 'averageBikingCadenceInRevPerMinute','avgPower','normPower',
                 'trainingStressScore','intensityFactor','lapCount','description','notes',
                 'directWorkoutRpe','directWorkoutFeel','isManualActivity','manualActivity',
                 'hrTimeInZone_1','hrTimeInZone_2','hrTimeInZone_3',
                 'hrTimeInZone_4','hrTimeInZone_5', 'powerTimeInZone_1',
                 'powerTimeInZone_2','powerTimeInZone_3','powerTimeInZone_4',
                 'powerTimeInZone_5','powerTimeInZone_6','powerTimeInZone_7')
# Explicit field allowlist: no GPS, device IDs, URLs, profile or coordinates.
METRIC_FIELDS = {
    'daily': ('calendarDate','totalSteps','totalDistanceMeters','restingHeartRate',
              'averageStressLevel','stressLevel','bodyBatteryMostRecentValue',
              'bodyBatteryHighestValue','bodyBatteryLowestValue',
              'activeKilocalories','moderateIntensityMinutes','vigorousIntensityMinutes'),
    'sleep': ('calendarDate','sleepTimeSeconds','sleepDuration','sleepScore',
              'overallSleepScore','deepSleepSeconds','lightSleepSeconds',
              'remSleepSeconds','awakeSleepSeconds','averageSleepStress',
              'avgSleepStress','sleepQualityType'),
    'hrv': ('calendarDate','weeklyAvg','lastNightAvg','lastNight5MinHigh',
             'baseline','status','hrvStatus','hrvValue','avgHrv'),
    'body_battery': ('calendarDate','bodyBatteryHigh','bodyBatteryLow',
                     'chargedValue','drainedValue','bodyBatteryMostRecentValue',
                     'bodyBatteryHighestValue','bodyBatteryLowestValue'),
}

