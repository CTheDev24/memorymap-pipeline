from memorymap_pipeline.config import load_config

config = load_config(None)  # Load default config
print("road_query_radius_m:", config.get("road_query_radius_m", None))
