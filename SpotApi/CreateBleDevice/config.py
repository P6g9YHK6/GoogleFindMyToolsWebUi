#
#  GoogleFindMyTools - A set of tools to interact with the Google Find My API
#  Copyright © 2024 Leon Böttger. All rights reserved.
#

import os

from SpotApi.CreateBleDevice.util import hours_to_seconds

mcu_fast_pair_model_id = "003200"
# Default 24h (was 4 days). Override with EID_UPLOAD_MAX_DAYS env var.
EID_UPLOAD_MAX_DAYS = int(os.environ.get("EID_UPLOAD_MAX_DAYS", "1"))
max_truncated_eid_seconds_server = hours_to_seconds(EID_UPLOAD_MAX_DAYS * 24)