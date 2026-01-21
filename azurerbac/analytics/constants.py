"""Analytics module constants."""

from typing import Final

# Threshold for considering a role "volatile" (min updates in rolling window)
VOLATILE_THRESHOLD: Final[int] = 3

# Number of top roles to display in lists
TOP_N_ROLES: Final[int] = 10

# Number of top providers to display
TOP_N_PROVIDERS: Final[int] = 15

# Number of days to show in daily changes chart
DAILY_CHANGES_DAYS: Final[int] = 180
