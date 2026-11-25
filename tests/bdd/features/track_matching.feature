Feature: TPS-to-Geographic Track Matching
  As a railway infrastructure analyst
  I want schematic TPS assets to match the exact Network Rail Appendix A track centreline
  So that signals, points, platforms, and edges align accurately on the geographic map

  Scenario: Match a Down Fast running-line signal to NWR track 2100
    Given an NWR corridor on ELR "MLN1" with candidate tracks:
      | track_id | start_m | end_m |
      | 1100     | 1000    | 3000  |
      | 2100     | 1000    | 3000  |
      | 2200     | 1000    | 3000  |
      | 2001     | 1000    | 3000  |
    When I score the candidates at 2000m for direction "down" and hint "Down Fast"
    Then the highest-scoring track should be "2100"

  Scenario: Match a numbered station platform to its dedicated NWR platform track
    Given an NWR corridor on ELR "SWM2" with candidate tracks:
      | track_id | start_m | end_m |
      | 1100     | 1000    | 3000  |
      | 2100     | 1000    | 3000  |
      | 3604     | 1800    | 2200  |
      | 3602     | 1800    | 2200  |
    When I score the candidates at 2000m for direction "reversible" and hint "Platform 4" on platform 4
    Then the highest-scoring track should be "3604"

  Scenario: Match a Stoke-on-Trent bi-directional SA platform signal using track identity
    Given TPS station track rows for "P1: Up Main" with platform code "SA"
    When I parse the station track identity with signal direction "down"
    Then the resolved direction should be "reversible"
    And the resolved secondary direction should be "up"
    And the resolved platform number should be 1

  Scenario: Clean and normalize raw TPS rail asset names
    Given a raw TPS signal name "53195-207_SIG: T3533"
    When I normalize it as asset type "signal"
    Then the normalized rail asset name should be "Signal T3533"

