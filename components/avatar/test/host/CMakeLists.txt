# SPDX-FileCopyrightText: 2026 sefuzhou770801-hub
# SPDX-License-Identifier: BSL-1.0
#
# Host-side tests for avatar animation helpers (ESP-IDF independent).

cmake_minimum_required(VERSION 3.16)
project(avatar_host_test CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Debug)
endif()

set(AVATAR_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../..)
set(REPO_ROOT ${AVATAR_ROOT}/../..)
set(VM_ROOT ${REPO_ROOT}/components/avatar_vm)

enable_testing()

add_executable(avatar_test_expression_controller test_expression_controller.cpp)
target_include_directories(avatar_test_expression_controller PRIVATE
    ${AVATAR_ROOT}/include
    ${VM_ROOT}/include
)
target_compile_options(avatar_test_expression_controller PRIVATE -Wall -Wextra)
add_test(NAME expression_controller COMMAND avatar_test_expression_controller)

# Multi-producer overlay publish tests (SharedState lives in main/).
add_executable(avatar_test_overlay_publish test_overlay_publish.cpp)
target_include_directories(avatar_test_overlay_publish PRIVATE
    ${AVATAR_ROOT}/include
    ${VM_ROOT}/include
    ${REPO_ROOT}/main
)
target_link_libraries(avatar_test_overlay_publish PRIVATE pthread)
target_compile_options(avatar_test_overlay_publish PRIVATE -Wall -Wextra)
add_test(NAME overlay_publish COMMAND avatar_test_overlay_publish)
