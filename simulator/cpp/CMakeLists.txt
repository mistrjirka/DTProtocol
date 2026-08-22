cmake_minimum_required(VERSION 3.16)
project(dtprotocol_host_sim LANGUAGES CXX)

option(DTP_HOST_SANITIZERS "Build host simulator with ASan/UBSan" OFF)

get_filename_component(REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)

enable_testing()

# -----------------------------------------------------------------------------
# Real DTPK + CrystDatabase + LCMM with a minimal MAC/Arduino shim. One process
# is one emulated MCU; Python owns propagation/topology/failures.
# -----------------------------------------------------------------------------
add_executable(dtprotocol_host_node
    runner.cpp
    host/host_runtime.cpp
    ${REPO_ROOT}/lib/DTPK/DTPK.cpp
    ${REPO_ROOT}/lib/DTPK/DTPKApi.cpp
    ${REPO_ROOT}/lib/DTPK/DTPKControl.cpp
    ${REPO_ROOT}/lib/DTPK/DTPKReceive.cpp
    ${REPO_ROOT}/lib/DTPK/DTPKFragment.cpp
    ${REPO_ROOT}/lib/DTPK/CrystDatabase.cpp
    ${REPO_ROOT}/lib/lcmm/lcmm.cpp
    ${REPO_ROOT}/lib/mathextension/mathextension.cpp
)

target_compile_features(dtprotocol_host_node PRIVATE cxx_std_17)
set_target_properties(dtprotocol_host_node PROPERTIES CXX_EXTENSIONS ON)

target_include_directories(dtprotocol_host_node BEFORE PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/host
    ${REPO_ROOT}
    ${REPO_ROOT}/include
)

target_compile_options(dtprotocol_host_node PRIVATE
    -include${CMAKE_CURRENT_SOURCE_DIR}/host/mac.h
    -include${REPO_ROOT}/include/mathextension.h
    -Wall -Wextra -Wno-unused-parameter
)
target_compile_definitions(dtprotocol_host_node PRIVATE HOST_SIM=1)

# -----------------------------------------------------------------------------
# LCMM contract against the same fake MAC used by the full host node. This
# specifically verifies that regulatory/carrier denials preserve retry budget
# while fatal radio errors terminate instead of being retried forever.
# -----------------------------------------------------------------------------
add_executable(dtprotocol_lcmm_contract
    lcmm_contract_tests.cpp
    host/host_runtime.cpp
    ${REPO_ROOT}/lib/lcmm/lcmm.cpp
    ${REPO_ROOT}/lib/mathextension/mathextension.cpp
)
target_compile_features(dtprotocol_lcmm_contract PRIVATE cxx_std_17)
set_target_properties(dtprotocol_lcmm_contract PROPERTIES CXX_EXTENSIONS ON)
target_include_directories(dtprotocol_lcmm_contract BEFORE PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/host
    ${REPO_ROOT}
    ${REPO_ROOT}/include
)
target_compile_options(dtprotocol_lcmm_contract PRIVATE
    -UNDEBUG
    -include${CMAKE_CURRENT_SOURCE_DIR}/host/mac.h
    -include${REPO_ROOT}/include/mathextension.h
    -Wall -Wextra -Wconversion -Wno-unused-parameter
)
target_compile_definitions(dtprotocol_lcmm_contract PRIVATE HOST_SIM=1)
add_test(NAME lcmm_mac_error_contract COMMAND dtprotocol_lcmm_contract)

# -----------------------------------------------------------------------------
# Compile and execute the *production* MAC implementation against a tiny
# RadioLib/SX1262 stub. This catches API/default/type/state errors that the fake
# MAC used by dtprotocol_host_node deliberately cannot see.
# -----------------------------------------------------------------------------
add_executable(dtprotocol_mac_contract
    mac_contract_tests.cpp
    host/mac_test_runtime.cpp
    ${REPO_ROOT}/lib/mac/mac.cpp
    ${REPO_ROOT}/lib/mathextension/mathextension.cpp
)

target_compile_features(dtprotocol_mac_contract PRIVATE cxx_std_17)
set_target_properties(dtprotocol_mac_contract PROPERTIES CXX_EXTENSIONS ON)
target_include_directories(dtprotocol_mac_contract BEFORE PRIVATE
    ${REPO_ROOT}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/host
    ${REPO_ROOT}
)
target_compile_options(dtprotocol_mac_contract PRIVATE
    -UNDEBUG
    -Wall -Wextra -Wconversion -Wno-unused-parameter
)
target_compile_definitions(dtprotocol_mac_contract PRIVATE HOST_SIM=1)

# Every invocation is a fresh process, which is important because production
# MAC is intentionally a singleton. Region selection is practical by default;
# one extra invocation pins the optional strict fallback-duty policy.
add_test(NAME production_mac_eu868
         COMMAND dtprotocol_mac_contract eu868)
add_test(NAME production_mac_eu868_strict_duty
         COMMAND dtprotocol_mac_contract eu868 strict-duty)
add_test(NAME production_mac_eu869_high_duty
         COMMAND dtprotocol_mac_contract eu869-high-duty)
add_test(NAME production_mac_eu433
         COMMAND dtprotocol_mac_contract eu433)

# -----------------------------------------------------------------------------
# Crystallization database contract: freshness (destination generation) must
# dominate metric among feasible candidates. This is a routing-safety/liveness
# invariant, independent of the RF adapter.
# -----------------------------------------------------------------------------
add_executable(dtprotocol_cryst_database_contract
    cryst_database_contract_tests.cpp
    ${REPO_ROOT}/lib/DTPK/CrystDatabase.cpp
)
target_compile_features(dtprotocol_cryst_database_contract PRIVATE cxx_std_17)
set_target_properties(dtprotocol_cryst_database_contract PROPERTIES CXX_EXTENSIONS ON)
target_include_directories(dtprotocol_cryst_database_contract PRIVATE
    ${REPO_ROOT}/include
)
target_compile_options(dtprotocol_cryst_database_contract PRIVATE
    -UNDEBUG
    -Wall -Wextra -Wconversion -Wno-unused-parameter
)
add_test(NAME crystallization_database_contract COMMAND dtprotocol_cryst_database_contract)

# Pure wire-format contracts: no Arduino, BLE stack, MAC or RadioLib needed.
add_executable(dtprotocol_bluetooth_protocol_contract
    bluetooth_protocol_contract_tests.cpp
)
target_compile_features(dtprotocol_bluetooth_protocol_contract PRIVATE cxx_std_17)
target_include_directories(dtprotocol_bluetooth_protocol_contract PRIVATE
    ${REPO_ROOT}/include
)
target_compile_options(dtprotocol_bluetooth_protocol_contract PRIVATE
    -UNDEBUG -Wall -Wextra -Wconversion
)
add_test(NAME bluetooth_protocol_contract COMMAND dtprotocol_bluetooth_protocol_contract)

add_executable(dtprotocol_multipart_contract
    multipart_contract_tests.cpp
)
target_compile_features(dtprotocol_multipart_contract PRIVATE cxx_std_17)
target_include_directories(dtprotocol_multipart_contract PRIVATE
    ${REPO_ROOT}/include
)
target_compile_options(dtprotocol_multipart_contract PRIVATE
    -UNDEBUG -Wall -Wextra -Wconversion
)
add_test(NAME multipart_wire_contract COMMAND dtprotocol_multipart_contract)

# -----------------------------------------------------------------------------
# Small embedded-math contract. The radio path depends on quickSort() during
# noise calibration and timeOnAir() for LCMM/duty timing, so both are sanitizer
# gates rather than untested helper code.
# -----------------------------------------------------------------------------
add_executable(dtprotocol_math_contract
    math_contract_tests.cpp
    host/mac_test_runtime.cpp
    ${REPO_ROOT}/lib/mathextension/mathextension.cpp
)
target_compile_features(dtprotocol_math_contract PRIVATE cxx_std_17)
set_target_properties(dtprotocol_math_contract PROPERTIES CXX_EXTENSIONS ON)
target_include_directories(dtprotocol_math_contract BEFORE PRIVATE
    ${REPO_ROOT}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/host
    ${REPO_ROOT}
)
target_compile_options(dtprotocol_math_contract PRIVATE
    -UNDEBUG
    -Wall -Wextra -Wconversion -Wno-unused-parameter
)
target_compile_definitions(dtprotocol_math_contract PRIVATE HOST_SIM=1)
add_test(NAME embedded_math_contract COMMAND dtprotocol_math_contract)

if(DTP_HOST_SANITIZERS)
    foreach(target
            dtprotocol_host_node
            dtprotocol_lcmm_contract
            dtprotocol_mac_contract
            dtprotocol_math_contract
            dtprotocol_cryst_database_contract
            dtprotocol_bluetooth_protocol_contract
            dtprotocol_multipart_contract)
        target_compile_options(${target} PRIVATE
            -fsanitize=address,undefined -fno-omit-frame-pointer)
        target_link_options(${target} PRIVATE -fsanitize=address,undefined)
    endforeach()
endif()
