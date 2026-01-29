def get_wards_feature_collections(wards_ids):
    for ward_id in wards_ids:
        ward_boundary = get_ward_boundary(ward_id)
        yield {
            "id": ward_id,
            "features": get_buildings_features(ward_boundary),
        }


def get_buildings_features(boundary_coords):
    # Based on https://github.com/dimagi/connect-gis/blob/1a47ddbf61d52be56a1bbc5caaeec651bc356f08/app.py#L165
    """
    Fetch buildings from the building table within the specified boundary polygon.

    Args:
        boundary_coords (list): List of [lng, lat] coordinates defining the boundary polygon.

    Returns:
        dict: GeoJSON FeatureCollection with building features.
    """
    if not boundary_coords:
        raise ValueError("Invalid boundary_coords coordinates")

    try:
        features = []
        for building in get_buildings_in_boundary(boundary_coords):
            # building will have a boundary attribute representing its polygon
            building_polygon = building.boundary
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [building_polygon],
                },
            }
            features.append(feature)

        return {"type": "FeatureCollection", "features": features}

    except Exception as e:
        print(f"Error fetching data: {e}")
        return {"type": "FeatureCollection", "features": []}


def get_ward_boundary(ward_id):
    # Todo: implement actual data fetching logic
    return [[]]


def get_buildings_in_boundary(boundary_coords):
    # Todo: implement actual data fetching logic
    return []
