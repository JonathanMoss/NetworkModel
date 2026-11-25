Feature: Network Rail Explorer API Workflows
  As an API consumer
  I want reliable endpoints for manual track overrides, Network Fusion profiles, and asset inspection
  So that the frontend map and audit tools work consistently

  Scenario: Save and delete a manual track override via the NWR API
    Given a mocked backend database for API requests
    When I POST a track override for asset type "signal", asset id 555, and nwr_fid 9001
    Then the HTTP response status should be 200
    And the JSON response field "ok" should be true
    When I DELETE the track override for asset type "signal" and asset id 555
    Then the HTTP response status should be 200

  Scenario: Requesting an unknown TPS asset type returns HTTP 400
    Given a mocked backend database for API requests
    When I GET "/api/asset/unknown_kind/123"
    Then the HTTP response status should be 400

  Scenario: Querying Network Fusion status reports ready state and counts
    Given a mocked backend database for Network Fusion status
    When I GET "/api/network-fusion/status"
    Then the HTTP response status should be 200
    And the JSON response field "status" should be "ready"

