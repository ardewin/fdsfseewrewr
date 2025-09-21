from .xui import XUIEngine
# from .hysteria import HysteriaEngine  # пример для будущих движков

def get_engine(name, config):
    if name == "xui":
        return XUIEngine(config)
    # elif name == "hysteria":
    #     return HysteriaEngine(config)
    raise ValueError(f"Unknown engine: {name}") 