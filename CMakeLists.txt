cmake_minimum_required(VERSION 3.22)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(stackchan_idf)

# Render-hot submodule: keep -O2 under the global -Os (OTA size budget).
idf_component_get_property(_m5gfx_lib M5GFX COMPONENT_LIB)
if(_m5gfx_lib)
    target_compile_options(${_m5gfx_lib} PRIVATE -O2)
endif()
