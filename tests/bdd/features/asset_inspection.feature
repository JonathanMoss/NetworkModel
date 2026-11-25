Feature: Interactive Asset Inspection Across TPS and NWR Models
  As a railway engineer exploring the UK network
  I want to inspect TPS stations, schematic vectors, and NWR centrelines through the API
  So that I can view decoded attributes and cross-model links in the inspector panel

  Scenario: Inspect a TPS graphic text label
    Given a mocked asset inspector database for graphictext 10 with label "READING"
    When I request GET "/api/asset/graphictext/10"
    Then the inspector status should be 200
    And the inspector title should contain "READING"

  Scenario: Inspect an NWR waymark with TPS cross-links
    Given a mocked NWR inspector database for waymark 7 on ELR "MLN1"
    When I request GET "/api/nwr/asset/nwr_waymark/7"
    Then the inspector status should be 200
    And the inspector title should contain "MLN1"

