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
    -Wall -Wextra -Wconversion -Wno-unused-parameter
)
target_compile_definitions(dtprotocol_mac_contract PRIVATE HOST_SIM=1)

# Every invocation is a fresh process, which is important because production
# MAC is intentionally a singleton. Exercise each regional profile separately.
add_test(NAME production_mac_eu868
         COMMAND dtprotocol_mac_contract eu868)
add_test(NAME production_mac_eu869_high_duty
         COMMAND dtprotocol_mac_contract eu869-high-duty)
add_test(NAME production_mac_eu433
         COMMAND dtprotocol_mac_contract eu433)

if(DTP_HOST_SANITIZERS)
    foreach(target dtprotocol_host_node dtprotocol_mac_contract)
        target_compile_options(${target} PRIVATE
            -fsanitize=address,undefined -fno-omit-frame-pointer)
        target_link_options(${target} PRIVATE -fsanitize=address,undefined)
    endforeach()
endif()
