def fix_type_to_text(fix_type: int) -> str:
    mapping = {
        0: "No Fix",
        1: "Dead Reckoning",
        2: "2D Fix",
        3: "3D Fix",
        4: "GNSS + DR",
        5: "Time Only",
    }
    return mapping.get(int(fix_type), "Unknown")


def carr_soln_to_text(carr_soln: int) -> str:
    mapping = {
        0: "No RTK",
        1: "RTK Float",
        2: "RTK Fixed",
    }
    return mapping.get(int(carr_soln), "Unknown")


def rtk_status_text(fix_type: int, carr_soln: int) -> str:
    if fix_type < 3:
        return "NO FIX"
    if carr_soln == 2:
        return "RTK FIXED"
    if carr_soln == 1:
        return "RTK FLOAT"
    return "3D (NO RTK)"
