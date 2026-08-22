cmake_minimum_required(VERSION 3.16)
project(dtprotocol_host_sim LANGUAGES CXX)

option(DTP_HOST_SANITIZERS "Build host simulator with ASan/UBSan" OFF)

get_filename_component(REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)

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

# Concatenate -include with its path: CMake otherwise de-duplicates repeated
# `-include` tokens and can leave the second header looking like a source file.
target_compile_options(dtprotocol_host_node PRIVATE
    -include${CMAKE_CURRENT_SOURCE_DIR}/host/mac.h
    -include${REPO_ROOT}/include/mathextension.h
    -Wall -Wextra -Wno-unused-parameter
)
target_compile_definitions(dtprotocol_host_node PRIVATE HOST_SIM=1)

if(DTP_HOST_SANITIZERS)
    target_compile_options(dtprotocol_host_node PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(dtprotocol_host_node PRIVATE -fsanitize=address,undefined)
endif()
