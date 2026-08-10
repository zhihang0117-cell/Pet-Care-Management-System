-- ALREADY EXECUTED against the live database on 2026-08-09 (via direct
-- Supabase REST calls, not by running this file) — kept here as a record
-- of what was done, not as a script to (re-)run. If you ever reuse this
-- shape for a similar cleanup, read the outcome note below first.
--
-- Confirmed 2026-08-09: near-total duplication across all three active
-- booking tables (seed data run twice) plus a handful of GENUINE
-- different-pet conflicts that exceed real room/staff capacity.
-- Rule applied throughout: keep whichever booking was created EARLIEST,
-- cancel the other(s) — applied uniformly, including the one already-Paid
-- pair (booking_id 196 vs its conflict), per explicit confirmation.
--
-- INTENDED: for every losing booking, set booking_status = 'Cancelled'
-- (keeping the row as an audit trail), then DELETE its linked payment row
-- and (if any) that payment's redemption row.
--
-- ACTUAL OUTCOME (important — do not assume the UPDATE below leaves a
-- Cancelled row behind): boarding_booking/grooming_booking/daycare_booking
-- all have payment_id REFERENCES payment(payment_id) ON DELETE CASCADE.
-- Deleting the payment row cascade-deleted the booking row too, even
-- though its status had just been set to Cancelled — the booking record
-- was not preserved, it was removed entirely (233 rows total: 88
-- boarding, 73 grooming, 72 daycare). Verified this only removed exactly
-- the intended candidate rows, nothing else, and every surviving/kept
-- booking (and its payment) is untouched. If you want an actual surviving
-- Cancelled record next time, delete the payment's redemption row and
-- NULL OUT (not delete) the payment linkage some other way, or accept the
-- cascade and don't bother with the booking_status update step at all.

-- ===== boarding_booking (88 bookings, 88 payments, 77 redemptions) =====
update boarding_booking
set booking_status = 'Cancelled'
where company_id = 1 and boarding_booking_id in (196, 202, 212, 226, 247, 256, 257, 262, 275, 276, 279, 280, 281, 283, 286, 288, 289, 290, 291, 292, 293, 295, 296, 297, 298, 299, 300, 301, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 312, 313, 314, 315, 316, 317, 318, 319, 320, 321, 322, 323, 324, 325, 326, 327, 328, 329, 330, 331, 332, 333, 334, 335, 336, 337, 338, 339, 340, 341, 342, 343, 344, 345, 346, 347, 348, 349, 350, 351, 352, 353, 354, 355, 356, 357, 358, 359, 360, 361)
  and booking_status in ('Pending', 'Scheduled');

delete from redemption
where company_id = 1 and redemption_id in (622, 637, 664, 699, 752, 775, 778, 790, 825, 828, 836, 838, 841, 846, 853, 860, 863, 868, 871, 876, 879, 881, 884, 887, 889, 892, 895, 897, 900, 904, 907, 911, 914, 919, 922, 924, 927, 930, 932, 935, 938, 940, 943, 946, 948, 951, 955, 958, 962, 965, 970, 973, 975, 978, 981, 983, 986, 989, 991, 994, 997, 999, 1002, 1006, 1009, 1013, 1016, 1021, 1024, 1026, 1029, 1032, 1034, 1037, 1040, 1042, 1045);

delete from payment
where company_id = 1 and payment_id in (713, 731, 761, 803, 866, 893, 896, 911, 950, 953, 962, 965, 968, 974, 983, 989, 992, 995, 998, 1001, 1004, 1010, 1013, 1016, 1019, 1022, 1025, 1028, 1031, 1034, 1037, 1040, 1043, 1046, 1049, 1052, 1055, 1058, 1061, 1064, 1067, 1070, 1073, 1076, 1079, 1082, 1085, 1088, 1091, 1094, 1097, 1100, 1103, 1106, 1109, 1112, 1115, 1118, 1121, 1124, 1127, 1130, 1133, 1136, 1139, 1142, 1145, 1148, 1151, 1154, 1157, 1160, 1163, 1166, 1169, 1172, 1175, 1178, 1181, 1184, 1187, 1190, 1193, 1196, 1199, 1202, 1205, 1208);

-- ===== grooming_booking (73 bookings, 73 payments, 63 redemptions) =====
update grooming_booking
set booking_status = 'Cancelled'
where company_id = 1 and grooming_booking_id in (475, 476, 477, 478, 479, 480, 481, 482, 483, 484, 485, 486, 487, 488, 489, 490, 491, 492, 493, 494, 495, 496, 497, 498, 499, 500, 501, 502, 503, 504, 505, 506, 507, 508, 509, 510, 511, 512, 513, 514, 515, 516, 517, 518, 519, 520, 521, 522, 523, 524, 525, 526, 527, 528, 529, 530, 531, 532, 533, 534, 535, 536, 537, 538, 539, 540, 541, 542, 543, 544, 545, 546, 549)
  and booking_status in ('Pending', 'Scheduled');

delete from redemption
where company_id = 1 and redemption_id in (861, 864, 866, 869, 872, 874, 877, 880, 882, 885, 888, 890, 893, 898, 901, 905, 908, 912, 915, 917, 920, 923, 925, 928, 931, 933, 936, 939, 941, 944, 949, 952, 956, 959, 963, 966, 968, 971, 974, 976, 979, 982, 984, 987, 990, 992, 995, 1000, 1003, 1007, 1010, 1014, 1017, 1019, 1022, 1025, 1027, 1030, 1033, 1035, 1038, 1041, 1043);

delete from payment
where company_id = 1 and payment_id in (993, 996, 999, 1002, 1005, 1008, 1011, 1014, 1017, 1020, 1023, 1026, 1029, 1032, 1035, 1038, 1041, 1044, 1047, 1050, 1053, 1056, 1059, 1062, 1065, 1068, 1071, 1074, 1077, 1080, 1083, 1086, 1089, 1092, 1095, 1098, 1101, 1104, 1107, 1110, 1113, 1116, 1119, 1122, 1125, 1128, 1131, 1134, 1137, 1140, 1143, 1146, 1149, 1152, 1155, 1158, 1161, 1164, 1167, 1170, 1173, 1176, 1179, 1182, 1185, 1188, 1191, 1194, 1197, 1200, 1203, 1206, 1212);

-- ===== daycare_booking (72 bookings, 72 payments, 60 redemptions) =====
update daycare_booking
set booking_status = 'Cancelled'
where company_id = 1 and daycare_booking_id in (301, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 312, 313, 314, 315, 316, 317, 318, 319, 320, 321, 322, 323, 324, 325, 326, 327, 328, 329, 330, 331, 332, 333, 334, 335, 336, 337, 338, 339, 340, 341, 342, 343, 344, 345, 346, 347, 348, 349, 350, 351, 352, 353, 354, 355, 356, 357, 358, 359, 360, 361, 362, 363, 364, 365, 366, 367, 368, 369, 370, 371, 372)
  and booking_status in ('Pending', 'Scheduled');

delete from redemption
where company_id = 1 and redemption_id in (862, 865, 867, 870, 875, 878, 883, 886, 891, 894, 896, 899, 902, 903, 906, 909, 910, 913, 916, 918, 921, 926, 929, 934, 937, 942, 945, 947, 950, 953, 954, 957, 960, 961, 964, 967, 969, 972, 977, 980, 985, 988, 993, 996, 998, 1001, 1004, 1005, 1008, 1011, 1012, 1015, 1018, 1020, 1023, 1028, 1031, 1036, 1039, 1044);

delete from payment
where company_id = 1 and payment_id in (994, 997, 1000, 1003, 1006, 1009, 1012, 1015, 1018, 1021, 1024, 1027, 1030, 1033, 1036, 1039, 1042, 1045, 1048, 1051, 1054, 1057, 1060, 1063, 1066, 1069, 1072, 1075, 1078, 1081, 1084, 1087, 1090, 1093, 1096, 1099, 1102, 1105, 1108, 1111, 1114, 1117, 1120, 1123, 1126, 1129, 1132, 1135, 1138, 1141, 1144, 1147, 1150, 1153, 1156, 1159, 1162, 1165, 1168, 1171, 1174, 1177, 1180, 1183, 1186, 1189, 1192, 1195, 1198, 1201, 1204, 1207);

-- TOTALS: 233 bookings cancelled, 233 payments deleted, 200 redemptions deleted.

-- Review after running, per table, e.g.:
--   select booking_status, count(*) from boarding_booking where company_id = 1 group by booking_status;