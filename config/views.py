from django.http import JsonResponse


def assetlinks_json(request):
    assetfile = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": "org.commcare.dalvik",
                "sha256_cert_fingerprints": [
                    "88:57:18:F8:E8:7D:74:04:97:AE:83:65:74:ED:EF:10:40:D9:4C:E2:54:F0:E0:40:64:77:96:7F:D1:39:F9:81",
                    "89:55:DF:D8:0E:66:63:06:D2:6D:88:A4:A3:88:A4:D9:16:5A:C4:1A:7E:E1:C6:78:87:00:37:55:93:03:7B:03",
                ],
            },
        },
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": "org.commcare.dalvik.debug",
                "sha256_cert_fingerprints": [
                    "88:57:18:F8:E8:7D:74:04:97:AE:83:65:74:ED:EF:10:40:D9:4C:E2:54:F0:E0:40:64:77:96:7F:D1:39:F9:81"
                ],
            },
        },
    ]
    return JsonResponse(assetfile, safe=False)
